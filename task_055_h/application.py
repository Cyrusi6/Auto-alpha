from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from data_pipeline.ashare.cache import TushareResponseCache
from task_055_f.network import _validate_records
from task_055_f.transport import evidence_use_identity, transport_identity
from task_055_g.causal import validate_fee_aware_causal_frontier
from task_055_g.network_state import PLAN_SCHEMA, consolidate
from task_055_g.truth import validate_truth_v2

from .authorization import validate_authorization_seal
from .contracts import RESPONSE_APPLY_SCHEMA
from .io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation
from .network import Task055HNetworkError


class Task055HApplicationError(RuntimeError):
    pass


def apply_native_canary_response(
    *,
    authorization_seal: str | Path,
    canary_acceptance: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    seal = validate_authorization_seal(authorization_seal, require_ready=True, verify_current_budget=False)
    seal_path = Path(authorization_seal).resolve()
    task_root = seal_path.parents[3]
    cache_data_root = task_root / str(seal["canonical_roots"]["cache_data_relative_to_output"])
    acceptance = validate_generation(
        canary_acceptance,
        schema="task055h_canary_acceptance_v1",
        manifest_name="canary_acceptance.json",
    )
    if acceptance.get("authorization_seal_content_hash") != seal["content_hash"]:
        raise Task055HApplicationError("canary_acceptance_authorization_mismatch")
    request = _request_from_seal(seal, acceptance["transport_hash"])
    cache = TushareResponseCache(cache_data_root, enabled=True)
    path = cache.cache_path(request["api_name"], params=request["params"], fields=request["fields"])
    if not path.is_file() or path.is_symlink() or sha256_file(path) != acceptance["cache_sha256"]:
        raise Task055HApplicationError("verified_cache_missing_or_drifted")
    envelope = read_json(path)
    reread = cache.read(
        request["api_name"],
        params=request["params"],
        fields=request["fields"],
        endpoint_schema_proof=envelope.get("endpoint_schema_proof"),
        allow_legacy_source_semantics=False,
    )
    if reread is None or not reread.hit:
        raise Task055HApplicationError("verified_cache_reread_failed")
    _validate_records(request, reread.records)
    if request["api_name"] == "daily":
        action = _daily_action(request, reread.records)
    elif request["api_name"] == "suspend_d":
        action = _suspend_action(request, reread.records)
    else:
        raise Task055HApplicationError("unsupported_response_api")
    response_rows = b"".join(
        (json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n").encode()
        for row in reread.records
    )
    response_partition = {
        "path": "verified_response_rows.jsonl",
        "sha256": __import__("hashlib").sha256(response_rows).hexdigest(),
        "row_count": len(reread.records),
    }
    semantic = {
        "schema_version": RESPONSE_APPLY_SCHEMA,
        "status": "applied",
        "authorization_seal_content_hash": seal["content_hash"],
        "canary_acceptance_content_hash": acceptance["content_hash"],
        "transport_hash": request["transport_hash"],
        "cache_sha256": acceptance["cache_sha256"],
        "response_item_count": len(reread.records),
        "response_partition": response_partition,
        "action": action,
        "rebuild_dag": _rebuild_dag(action, seal),
        "parent_artifact_catalog_root": canonical_hash(seal.get("artifact_sha_catalog") or ()),
        "semantic_source_root": seal.get("semantic_source_root"),
        "production_inputs_from_sealed_native_artifacts_only": True,
        "external_mapping_accepted": False,
    }
    published = publish_generation(
        output_root,
        prefix="response_apply",
        manifest_name="response_apply.json",
        semantic=semantic,
        extra_files={"verified_response_rows.jsonl": response_rows},
    )
    validate_native_response_apply(published["manifest_path"])
    return published


def validate_native_response_apply(path: str | Path) -> dict[str, Any]:
    payload = validate_generation(path, schema=RESPONSE_APPLY_SCHEMA, manifest_name="response_apply.json")
    if payload.get("production_inputs_from_sealed_native_artifacts_only") is not True or payload.get("external_mapping_accepted") is not False:
        raise Task055HApplicationError("response_apply_native_input_boundary_invalid")
    partition = payload.get("response_partition") or {}
    rows_path = Path(payload["manifest_path"]).parent / str(partition.get("path") or "")
    if (
        not rows_path.is_file()
        or rows_path.is_symlink()
        or sha256_file(rows_path) != partition.get("sha256")
        or len([line for line in rows_path.read_text(encoding="utf-8").splitlines() if line]) != int(partition.get("row_count") or 0)
    ):
        raise Task055HApplicationError("response_apply_partition_invalid")
    dag = payload.get("rebuild_dag") or {}
    stages = list(dag.get("stages") or ())
    if not stages or dag.get("dag_hash") != canonical_hash({key: value for key, value in dag.items() if key != "dag_hash"}):
        raise Task055HApplicationError("response_apply_dag_invalid")
    if any(not row.get("public_fqn") or not row.get("required_outputs") for row in stages):
        raise Task055HApplicationError("response_apply_stage_contract_invalid")
    return payload


def build_dynamic_l2_from_l1_empty(
    *,
    authorization_seal: str | Path,
    l1_apply: str | Path,
    rebuilt_truth: str | Path,
    rebuilt_causal_frontier: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    seal = validate_authorization_seal(authorization_seal, require_ready=True, verify_current_budget=False)
    applied = validate_native_response_apply(l1_apply)
    action = applied.get("action") or {}
    if action.get("kind") != "vendor_daily_absence" or applied.get("authorization_seal_content_hash") != seal["content_hash"]:
        raise Task055HApplicationError("l2_requires_applied_l1_empty")
    truth = validate_truth_v2(rebuilt_truth)
    causal = validate_fee_aware_causal_frontier(rebuilt_causal_frontier)
    truth_lineage = truth.get("lineage") or {}
    causal_lineage = causal.get("lineage") or {}
    if (
        truth_lineage.get("parent_response_apply_content_hash") != applied["content_hash"]
        or truth_lineage.get("verified_cache_sha256") != applied["cache_sha256"]
        or causal_lineage.get("truth_v2_content_hash") != truth["content_hash"]
        or causal_lineage.get("parent_response_apply_content_hash") != applied["content_hash"]
    ):
        raise Task055HApplicationError("l2_rebuild_lineage_invalid")
    key = action["security_date"]
    if [key["ts_code"], key["trade_date"]] not in [list(item) for item in causal["frontier_keys"]]:
        raise Task055HApplicationError("l2_key_resolved_after_l1_rebuild")
    params = {"ts_code": key["ts_code"], "trade_date": key["trade_date"]}
    fields = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
    transport_hash = transport_identity("suspend_d", params, fields)
    request = {
        "stage": "L2",
        "api_name": "suspend_d",
        "params": params,
        "fields": fields,
        "ts_code": params["ts_code"],
        "trade_date": params["trade_date"],
        "transport_hash": transport_hash,
        "evidence_use_hash": evidence_use_identity(
            stage="L2",
            parent_plan_hash=applied["content_hash"],
            frontier_root=canonical_hash([[params["ts_code"], params["trade_date"]]]),
            transport_hash=transport_hash,
        ),
    }
    lineage = dict(seal["task055g_plan_lineage"]) | {
        "parent_l1_apply_hash": applied["content_hash"],
        "response_lineage_root": canonical_hash([applied["transport_hash"], applied["cache_sha256"]]),
        "truth_content_hash": truth["content_hash"],
        "fee_aware_causal_frontier_content_hash": causal["content_hash"],
        "frontier_root": canonical_hash([[params["ts_code"], params["trade_date"]]]),
        "key_root": canonical_hash([[params["ts_code"], params["trade_date"]]]),
    }
    semantic = {
        "schema_version": PLAN_SCHEMA,
        "status": "sealed_exact_suspend_l2",
        "stage": "L2",
        "round_id": 1,
        "frontier_root": lineage["frontier_root"],
        "parent_apply_hash": applied["content_hash"],
        "lineage": lineage,
        "parent_l1_apply_hash": applied["content_hash"],
        "authorization_seal_content_hash": seal["content_hash"],
        "requests": [request],
        "limits": seal["budgets"]["limits"],
        "empty_response_semantics": "vendor_absence_only_not_full_day_suspension_proof",
    }
    semantic["plan_hash"] = canonical_hash(semantic)
    published = publish_generation(output_root, prefix="dynamic_l2_plan", manifest_name="dynamic_l2_plan.json", semantic=semantic)
    task_root = Path(authorization_seal).resolve().parents[3]
    registration = consolidate(
        state_root=task_root / str(seal["canonical_roots"]["state_relative_to_output"]),
        plan_manifest=published["manifest_path"],
    )
    return published | {
        "registered_network_ledger_root": (registration.get("ledger") or {}).get("ledger_root"),
        "registered_logical_request_count": (registration.get("ledger") or {}).get("request_count"),
    }


def synthetic_test_only_apply(
    *,
    api_name: str,
    records: list[Mapping[str, Any]],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Explicit test-only state transition; never accepted by production seals."""

    _validate_records(request, records)
    action = _daily_action(request, records) if api_name == "daily" else _suspend_action(request, records)
    stages = ["verified_cache", "truth_rebuild"]
    if action["kind"] == "immutable_daily_raw_repair":
        stages.extend(["immutable_freeze", "strict_matrix", "v3_tensor", "research_firewall_sentinel", "exact20_x5_causal_replay"])
    elif action["kind"] == "vendor_daily_absence":
        stages.append("dynamic_exact_suspend_l2")
    else:
        stages.extend(["evidence_overlay", "exact20_x5_causal_replay"])
    return {
        "evidence_scope": "synthetic_test_only",
        "production_seal_eligible": False,
        "action": action,
        "stages": stages,
        "next_frontier_root": canonical_hash([action]),
    }


def _daily_action(request: Mapping[str, Any], records: list[Mapping[str, Any]]) -> dict[str, Any]:
    key = {"ts_code": request["params"]["ts_code"], "trade_date": request["params"]["trade_date"]}
    if not records:
        return {
            "kind": "vendor_daily_absence",
            "security_date": key,
            "proves_suspension": False,
            "next_stage": "truth_rebuild_then_dynamic_exact_suspend_l2",
        }
    if len(records) != 1:
        raise Task055HApplicationError("exact_daily_response_cardinality_invalid")
    row = dict(records[0])
    return {
        "kind": "immutable_daily_raw_repair",
        "security_date": key,
        "row_hash": canonical_hash(row),
        "provenance": {
            "transport_hash": request["transport_hash"],
            "response_geometry": "exact_security_date",
            "immutable_sibling_required": True,
        },
        "required_rebuild_chain": [
            "governed_raw_repair_generation",
            "immutable_freeze",
            "strict_matrix",
            "v3_tensor",
            "research_firewall_sentinel",
            "frozen_exact20_materialization_and_replay",
            "fee_aware_causal_frontier",
        ],
    }


def _suspend_action(request: Mapping[str, Any], records: list[Mapping[str, Any]]) -> dict[str, Any]:
    key = {"ts_code": request["params"]["ts_code"], "trade_date": request["params"]["trade_date"]}
    types = sorted({str(row.get("suspend_type")) for row in records})
    timings = sorted({"<null>" if row.get("suspend_timing") is None else str(row.get("suspend_timing")) for row in records})
    if not records:
        outcome = "vendor_suspend_absence_not_no_trade_proof"
    elif types == ["S"]:
        outcome = "modeled_suspend_candidate_timing_uncertified"
    elif types == ["R"]:
        outcome = "resume_event_not_suspension_proof"
    else:
        outcome = "suspend_event_conflict_blocked"
    return {
        "kind": "suspend_evidence_overlay",
        "security_date": key,
        "suspend_types": types,
        "timing_values": timings,
        "outcome": outcome,
        "certification_blocker_retained": True,
    }


def _request_from_seal(seal: Mapping[str, Any], transport_hash: str) -> dict[str, Any]:
    matches = [row for row in seal["ordered_exact_daily_keys"] if row["transport_hash"] == transport_hash]
    if len(matches) != 1:
        raise Task055HApplicationError("response_transport_not_in_seal")
    row = matches[0]
    return {
        "api_name": row["api_name"],
        "params": {"ts_code": row["ts_code"], "trade_date": row["trade_date"]},
        "fields": row["fields"],
        "ts_code": row["ts_code"],
        "trade_date": row["trade_date"],
        "transport_hash": row["transport_hash"],
        "evidence_use_hash": row["evidence_use_hash"],
    }


def _rebuild_dag(action: Mapping[str, Any], seal: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(action.get("kind") or "")
    common = {
        "authorization_seal_content_hash": seal["content_hash"],
        "exact20_identity_source": "sealed_task055g_parent",
        "candidate_reselection_allowed": False,
        "budget_reset_allowed": False,
    }
    if kind == "immutable_daily_raw_repair":
        stages = [
            _stage("raw_repair", "task_055_h.application.apply_native_canary_response", ["verified_response_rows", "immutable_raw_repair_delta"]),
            _stage("immutable_freeze", "data_lake.task052_freeze.create_task052_governed_freeze", ["governed_freeze_manifest"]),
            _stage("strict_matrix", "matrix_store.strict_engineering.StrictEngineeringPITMatrixBuilder.build", ["strict_matrix_manifest", "partition_sha_catalog"]),
            _stage("v3_tensor", "task_053_a.orchestrator.build_v3_tensor_generation", ["v3_tensor_manifest", "values_sha", "validity_sha"]),
            _stage("research_firewall", "task_054_b.sentinel.run_task054b_production_sentinel", ["sentinel_manifest", "research_semantic_root"]),
            _stage("exact20_materialization", "validation_lab.materialization.FactorMaterializer.materialize", ["exact20_materialization_root"]),
            _stage("fee_aware_causal_frontier", "task_055_g.causal.build_fee_aware_causal_frontier", ["causal_frontier_manifest", "next_frontier_root"]),
        ]
        branch = "positive_daily_raw_repair_full_lineage_rebuild"
    elif kind == "vendor_daily_absence":
        stages = [
            _stage("vendor_absence_evidence", "task_055_h.application.apply_native_canary_response", ["verified_empty_daily_attestation"]),
            _stage("truth_rebuild", "task_055_g.truth.build_truth_v2", ["truth_v2_manifest"]),
            _stage("fee_aware_causal_frontier", "task_055_g.causal.build_fee_aware_causal_frontier", ["causal_frontier_manifest", "next_frontier_root"]),
            _stage("dynamic_l2", "task_055_h.application.build_dynamic_l2_from_l1_empty", ["dynamic_l2_plan"]),
        ]
        branch = "empty_daily_vendor_absence_then_dynamic_l2"
    else:
        stages = [
            _stage("suspension_evidence_overlay", "task_055_h.application.apply_native_canary_response", ["suspension_evidence_overlay"]),
            _stage("truth_rebuild", "task_055_g.truth.build_truth_v2", ["truth_v2_manifest"]),
            _stage("fee_aware_causal_frontier", "task_055_g.causal.build_fee_aware_causal_frontier", ["causal_frontier_manifest", "next_frontier_root"]),
        ]
        branch = "suspend_semantic_reconciliation"
    dag = common | {"branch": branch, "stages": stages}
    return dag | {"dag_hash": canonical_hash(dag)}


def _stage(stage: str, public_fqn: str, required_outputs: list[str]) -> dict[str, Any]:
    return {
        "stage": stage,
        "public_fqn": public_fqn,
        "required_outputs": required_outputs,
        "native_artifacts_only": True,
        "immutable_generation_required": True,
    }
