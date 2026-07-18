from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from data_lake.task052_freeze import validate_task052_governed_freeze
from factor_store.storage import LocalFactorStore
from matrix_store.strict_engineering import StrictEngineeringPITMatrixBuilder, StrictEngineeringPITMatrixConfig
from task_053_a.orchestrator import build_v3_tensor_generation
from task_054_b.sentinel import (
    ProductionSentinelConfig,
    run_task054b_production_sentinel,
    validate_task054b_production_sentinel,
)
from task_054_c.factor_store import validate_normalized_replay_store
from task_055_a.bundle import EXECUTION_MASKS, SIGNAL_MASKS, load_simulation_bundle, validate_simulation_bundle
from task_055_a.run import prepare_simulation_inputs
from task_055_f.causal import build_valuation_surface, trace_causal_runs
from task_055_f.valuation import load_valuation_projection, publish_valuation_projection
from task_055_g.truth import publish_truth_successor, validate_truth_v2
from task_055_h.fee import FeeProjectionCalculator
from task_055_h.independent import independently_trace_prepared
from task_055_h.io import atomic_json, canonical_hash, publish_generation, read_json, sha256_file, validate_generation
from validation_lab.materialization import FactorMaterializer

from task_055_i.application import (
    _assert_repair_in_matrix,
    _build_repaired_freeze,
    _materialize_exact20,
    _publish_raw_repair,
)

from .contracts import APPLICATION_SCHEMA, CANARY, CAUSAL_REPLAY_SCHEMA, MAX_DATE
from .executor import load_accepted_canary_cache


class Task055JApplicationError(RuntimeError):
    pass


def apply_accepted_canary(
    *,
    final_execution_seal: str | Path,
    reviewed_final_execution_seal_hash: str,
    canary_acceptance: str | Path,
) -> dict[str, Any]:
    accepted = load_accepted_canary_cache(
        final_execution_seal=final_execution_seal,
        reviewed_final_execution_seal_hash=reviewed_final_execution_seal_hash,
        canary_acceptance=canary_acceptance,
    )
    context = _production_context(accepted["final_execution_seal"])
    return _apply(
        accepted=accepted,
        context=context,
        output_root=Path(accepted["authority_root"]) / "applications",
        evidence_scope="real_production",
    )


def validate_native_application(path: str | Path, *, authority_root: str | Path) -> dict[str, Any]:
    payload = validate_generation(path, schema=APPLICATION_SCHEMA, manifest_name="response_application.json")
    root = Path(authority_root).resolve()
    if payload.get("status") != "applied":
        raise Task055JApplicationError("task055j_application_status_invalid")
    if payload.get("evidence_scope") not in {"real_production", "synthetic_rehearsal_only"}:
        raise Task055JApplicationError("task055j_application_scope_invalid")
    if payload.get("evidence_scope") == "synthetic_rehearsal_only" and payload.get("production_seal_eligible") is not False:
        raise Task055JApplicationError("task055j_synthetic_application_seal_boundary_invalid")
    if int(payload.get("terminal_pair_count") or 0) != 100:
        raise Task055JApplicationError("task055j_application_exact20_x5_invalid")
    catalog = list(payload.get("artifact_catalog") or ())
    if not catalog or canonical_hash(catalog) != payload.get("artifact_catalog_root"):
        raise Task055JApplicationError("task055j_application_catalog_invalid")
    for row in catalog:
        relative = Path(str(row.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise Task055JApplicationError("task055j_application_catalog_path_invalid")
        artifact = (root / relative).resolve()
        if root not in artifact.parents or not artifact.is_file() or artifact.is_symlink():
            raise Task055JApplicationError("task055j_application_artifact_missing_or_escape")
        if artifact.stat().st_size != row.get("size_bytes") or sha256_file(artifact) != row.get("sha256"):
            raise Task055JApplicationError(f"task055j_application_artifact_drift:{relative}")
    replay_path = root / str(payload["native_replay_manifest_relative_path"])
    replay = validate_native_causal_replay(replay_path)
    if replay["content_hash"] != payload["stage_outputs"]["fee_aware_exact20_x5"]:
        raise Task055JApplicationError("task055j_application_replay_lineage_invalid")
    truth = validate_truth_v2(root / str(payload["truth_manifest_relative_path"]))
    if truth["content_hash"] != payload["stage_outputs"]["truth"]:
        raise Task055JApplicationError("task055j_application_truth_lineage_invalid")
    _independently_verify_truth_successor(root, payload, truth)
    return payload | {"replay": replay, "truth": truth}


def validate_native_causal_replay(path: str | Path) -> dict[str, Any]:
    payload = validate_generation(path, schema=CAUSAL_REPLAY_SCHEMA, manifest_name="native_causal_replay.json")
    root = Path(payload["manifest_path"]).parent
    partitions = payload.get("partitions") or {}
    rows = _read_jsonl(root / partitions["run_rows"]["path"])
    held = _read_jsonl(root / partitions["held_marks"]["path"])
    independent = read_json(root / partitions["independent_verification"]["path"])
    for entry in partitions.values():
        artifact = root / str(entry["path"])
        if not artifact.is_file() or sha256_file(artifact) != entry["sha256"]:
            raise Task055JApplicationError("task055j_native_replay_partition_invalid")
    pairs = [(row["factor_id"], row["scenario"]) for row in rows]
    expected = sorted((factor_id, scenario) for factor_id in payload["exact20_ids"] for scenario in payload["scenarios"])
    if len(rows) != 100 or sorted(pairs) != expected or len(pairs) != len(set(pairs)):
        raise Task055JApplicationError("task055j_native_replay_cartesian_invalid")
    if canonical_hash(rows) != payload.get("run_rows_root") or canonical_hash(held) != payload.get("held_mark_root"):
        raise Task055JApplicationError("task055j_native_replay_roots_invalid")
    if independent.get("producer_run_rows_root") != payload["run_rows_root"] or independent.get("producer_held_mark_root") != payload["held_mark_root"]:
        raise Task055JApplicationError("task055j_native_replay_independent_roots_invalid")
    return payload | {"run_rows": rows, "held_marks": held, "independent_verification": independent}


def _apply(
    *, accepted: Mapping[str, Any], context: Mapping[str, Any], output_root: Path, evidence_scope: str
) -> dict[str, Any]:
    request = dict(accepted["request"])
    records = [dict(row) for row in accepted["records"]]
    if request.get("api_name") != "daily" or request.get("trade_date") > MAX_DATE:
        raise Task055JApplicationError("task055j_application_exact_daily_required")
    output_root.mkdir(parents=True, exist_ok=True)
    lock_path = output_root.parent / "application.lock"
    with lock_path.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            spec_hash = canonical_hash(
                [
                    accepted["acceptance"]["content_hash"],
                    context["context_root"],
                    stable_response_identity(accepted),
                    evidence_scope,
                ]
            )
            existing = _find_existing_application(output_root, spec_hash)
            if existing is not None:
                return validate_native_application(existing, authority_root=output_root.parent)
            stage_root = output_root / "stages" / f"application_{spec_hash[:24]}"
            stage_root.mkdir(parents=True, exist_ok=True)
            journal_path = stage_root / "stage_journal.json"
            journal = _load_or_initialize_stage_journal(journal_path, spec_hash=spec_hash)
            if journal.get("status") == "completed":
                stage, action = _load_completed_stage(stage_root, records=records, journal=journal)
            else:
                if records:
                    stage = _apply_positive(
                        accepted=accepted,
                        context=context,
                        stage_root=stage_root,
                        evidence_scope=evidence_scope,
                    )
                    action = "immutable_daily_raw_repair"
                else:
                    stage = _apply_empty(
                        accepted=accepted,
                        context=context,
                        stage_root=stage_root,
                        evidence_scope=evidence_scope,
                    )
                    action = "vendor_daily_absence_not_no_trade_proof"
                atomic_json(journal_path, {"status": "completed", "spec_hash": spec_hash, "stage_outputs": stage["stage_outputs"]})
            catalog = _catalog(output_root.parent, stage["artifacts"] + [journal_path])
            semantic = {
                "schema_version": APPLICATION_SCHEMA,
                "status": "applied",
                "evidence_scope": evidence_scope,
                "production_seal_eligible": evidence_scope == "real_production",
                "final_execution_seal_content_hash": accepted["final_execution_seal"]["content_hash"],
                "canary_acceptance_content_hash": accepted["acceptance"]["content_hash"],
                "request": {key: request[key] for key in ("api_name", "ts_code", "trade_date", "fields", "transport_hash", "evidence_use_hash")},
                "response_item_count": len(records),
                "action": action,
                "application_spec_hash": spec_hash,
                "context_root": context["context_root"],
                "stage_outputs": stage["stage_outputs"],
                "truth_manifest_relative_path": Path(stage["truth_manifest"]).relative_to(output_root.parent).as_posix(),
                "native_replay_manifest_relative_path": Path(stage["replay_manifest"]).relative_to(output_root.parent).as_posix(),
                "artifact_catalog": catalog,
                "artifact_catalog_root": canonical_hash(catalog),
                "terminal_pair_count": stage["terminal_pair_count"],
                "terminal_counts": stage["terminal_counts"],
                "net_frontier_root": stage["net_frontier_root"],
                "all_in_frontier_root": stage["all_in_frontier_root"],
                "frontier_union_root": stage["frontier_union_root"],
                "next_frontier_keys": stage["frontier_union"],
                "candidate_reselection_allowed": False,
                "resume_authorized": False,
                "certification_ready": False,
                "portfolio_ready": False,
                "optimizer_ready": False,
                "paper_ready": False,
                "live_ready": False,
            }
            result = publish_generation(
                output_root,
                prefix="task055j_response_application",
                manifest_name="response_application.json",
                semantic=semantic,
            )
            return validate_native_application(result["manifest_path"], authority_root=output_root.parent)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _find_existing_application(output_root: Path, spec_hash: str) -> Path | None:
    candidates = sorted((output_root / "generations").glob("*/response_application.json"))
    matches = []
    for candidate in candidates:
        payload = read_json(candidate)
        if payload.get("application_spec_hash") == spec_hash:
            matches.append(candidate)
    if len(matches) > 1:
        raise Task055JApplicationError("task055j_application_duplicate_generation")
    return matches[0] if matches else None


def _load_or_initialize_stage_journal(journal_path: Path, *, spec_hash: str) -> dict[str, Any]:
    if journal_path.is_file():
        journal = read_json(journal_path)
        if journal.get("spec_hash") != spec_hash:
            raise Task055JApplicationError("task055j_application_stage_spec_drift")
        return journal
    journal = {"status": "started", "spec_hash": spec_hash}
    atomic_json(journal_path, journal)
    return journal


def _independently_verify_truth_successor(
    authority_root: Path, application: Mapping[str, Any], successor: Mapping[str, Any]
) -> None:
    acceptance = _find_generation_by_hash(
        authority_root / "acceptance",
        "canary_acceptance.json",
        str(application["canary_acceptance_content_hash"]),
    )
    receipt = _find_generation_by_hash(
        authority_root / "transport_receipts",
        "transport_receipt.json",
        str(acceptance["transport_receipt_content_hash"]),
    )
    cache_relative = Path(str(acceptance.get("cache_relative_path") or ""))
    cache_path = (authority_root / cache_relative).resolve()
    if authority_root not in cache_path.parents or cache_path.is_symlink() or not cache_path.is_file():
        raise Task055JApplicationError("task055j_application_cache_evidence_invalid")
    if sha256_file(cache_path) != acceptance.get("cache_sha256"):
        raise Task055JApplicationError("task055j_application_cache_evidence_sha_invalid")
    cache = read_json(cache_path)
    records = [dict(row) for row in cache.get("records") or ()]
    if records != list(receipt.get("records") or ()) or len(records) != application.get("response_item_count"):
        raise Task055JApplicationError("task055j_application_response_evidence_mismatch")
    runtime = _runtime_for_application(authority_root, application)
    governed = Path(runtime.get("governed_root") or authority_root.parents[2]).resolve()
    catalog = {str(row["role"]): row for row in runtime["application_artifacts"]["catalog"]}
    parent_truth_path = (governed / str(catalog["truth_v2"]["relative_path"])).resolve()
    if governed not in parent_truth_path.parents or sha256_file(parent_truth_path) != catalog["truth_v2"]["sha256"]:
        raise Task055JApplicationError("task055j_application_parent_truth_evidence_invalid")
    parent = validate_truth_v2(parent_truth_path)
    parent_rows = {
        (str(row["ts_code"]), str(row["trade_date"])): dict(row)
        for row in parent["records"]
    }
    successor_rows = {
        (str(row["ts_code"]), str(row["trade_date"])): dict(row)
        for row in successor["records"]
    }
    if len(parent_rows) != 35844 or set(successor_rows) != set(parent_rows):
        raise Task055JApplicationError("task055j_application_truth_key_set_invalid")
    request = dict(application["request"])
    key = (str(request["ts_code"]), str(request["trade_date"]))
    for current_key, parent_row in parent_rows.items():
        if current_key != key and successor_rows[current_key] != parent_row:
            raise Task055JApplicationError("task055j_application_truth_unrelated_row_changed")
    expected = _expected_truth_successor_row(
        parent_rows[key],
        request=request,
        records=records,
        cache_sha256=str(acceptance["cache_sha256"]),
        receipt_content_hash=str(receipt["content_hash"]),
        acceptance_content_hash=str(acceptance["content_hash"]),
    )
    if successor_rows[key] != expected:
        raise Task055JApplicationError("task055j_application_truth_transition_invalid")
    lineage = successor.get("lineage") or {}
    if lineage.get("parent_truth_content_hash") != parent["content_hash"] or lineage.get("updated_security_date") != list(key):
        raise Task055JApplicationError("task055j_application_truth_parent_lineage_invalid")


def _expected_truth_successor_row(
    parent_row: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    cache_sha256: str,
    receipt_content_hash: str,
    acceptance_content_hash: str,
) -> dict[str, Any]:
    current = dict(parent_row)
    proof = {
        "api": "daily",
        "source_kind": "task055j_native_accepted_cache",
        "proof_quality": "validated_task055j_transport_receipt_and_v3_cache",
        "outcome": "matching_row" if records else "no_matching_row",
        "request_fingerprint": request["transport_hash"],
        "source_sha256": cache_sha256,
        "transport_receipt_content_hash": receipt_content_hash,
        "parent_apply_hash": acceptance_content_hash,
    }
    proof["proof_hash"] = canonical_hash(proof)
    evidence = [dict(row) for row in current.get("daily_response_evidence") or ()]
    evidence = [row for row in evidence if row.get("proof_hash") != proof["proof_hash"]]
    evidence.append(proof)
    current["daily_response_evidence"] = sorted(evidence, key=lambda row: str(row.get("proof_hash")))
    if records:
        raw = dict(records[0])
        if current.get("corporate_action_validity") is False:
            state, reason = "LIFECYCLE_OR_CORPORATE_ACTION_CONFLICT", "new_complete_daily_bar_with_corporate_action_conflict"
        elif not current.get("listed") or not current.get("active"):
            state, reason = "MATRIX_SOURCE_CONFLICT", "new_complete_daily_bar_conflicts_with_lifecycle_or_inventory"
        elif current.get("suspend_type") in {"S", "S+R"}:
            state, reason = "MATRIX_SOURCE_CONFLICT", "new_complete_daily_bar_conflicts_with_suspend_event"
        else:
            state, reason = "TRADED_PRIMARY_BAR", "task055j_verified_exact_daily_bar"
        current.update(
            {
                "state": state,
                "reason_code": reason,
                "daily_bar_status": "present_complete",
                "matrix_bar": {
                    "open": raw.get("open"),
                    "high": raw.get("high"),
                    "low": raw.get("low"),
                    "close": raw.get("close"),
                    "pre_close": raw.get("pre_close"),
                    "volume": raw.get("vol"),
                    "amount": raw.get("amount"),
                },
                "inventory_bar_observed": True,
                "modeled_stale_candidate": False,
                "stale_mark_authorized": False,
                "task055j_response_application": "positive_daily",
            }
        )
    else:
        current["task055j_response_application"] = "vendor_daily_absence_not_no_trade_proof"
        current["task055j_vendor_daily_absence"] = True
    current.pop("evidence_hash", None)
    current["evidence_hash"] = canonical_hash(current)
    return current


def _runtime_for_application(authority_root: Path, application: Mapping[str, Any]) -> dict[str, Any]:
    seal = _find_generation_by_hash(
        authority_root / "final_execution_seal",
        "final_execution_seal.json",
        str(application["final_execution_seal_content_hash"]),
    )
    embedded = seal.get("runtime_authority")
    if isinstance(embedded, dict):
        return dict(embedded)
    return _find_generation_by_hash(
        authority_root / "runtime_authority",
        "runtime_authority.json",
        str(seal["runtime_authority_content_hash"]),
    )


def _find_generation_by_hash(root: Path, manifest_name: str, content_hash: str) -> dict[str, Any]:
    matches = []
    for path in sorted((root / "generations").glob(f"*/{manifest_name}")):
        payload = read_json(path)
        if payload.get("content_hash") == content_hash:
            matches.append(payload | {"manifest_path": str(path)})
    if len(matches) != 1:
        raise Task055JApplicationError(f"task055j_application_generation_resolution_invalid:{manifest_name}")
    return matches[0]


def _load_completed_stage(
    stage_root: Path, *, records: Sequence[Mapping[str, Any]], journal: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    truth_path = _current_manifest(stage_root / "truth", "truth_v2_manifest.json")
    replay_path = _current_manifest(stage_root / "native_replay/manifest", "native_causal_replay.json")
    truth = validate_truth_v2(truth_path)
    replay = validate_native_causal_replay(replay_path)
    outputs = dict(journal.get("stage_outputs") or {})
    if outputs.get("truth") != truth["content_hash"] or outputs.get("fee_aware_exact20_x5") != replay["content_hash"]:
        raise Task055JApplicationError("task055j_completed_stage_lineage_drift")
    artifacts = [stage_root / "truth", stage_root / "native_replay"]
    if records:
        required = ("raw_repair", "freeze", "matrix", "tensor", "exact20_materialization", "firewall_sentinel")
        if any(not outputs.get(name) for name in required):
            raise Task055JApplicationError("task055j_completed_positive_stage_incomplete")
        artifacts.extend(
            stage_root / name
            for name in ("raw_repair", "freeze", "matrix", "tensor", "materializations", "firewall_sentinel")
        )
        action = "immutable_daily_raw_repair"
    else:
        l2 = validate_generation(
            _current_manifest(stage_root / "dynamic_l2", "dynamic_l2_plan.json"),
            schema="task055j_dynamic_exact_suspend_l2_v1",
            manifest_name="dynamic_l2_plan.json",
        )
        if outputs.get("dynamic_l2") != l2["content_hash"]:
            raise Task055JApplicationError("task055j_completed_empty_stage_l2_drift")
        artifacts.append(stage_root / "dynamic_l2")
        action = "vendor_daily_absence_not_no_trade_proof"
    return (
        _stage_result(truth=truth, replay=replay, artifacts=artifacts, extra_outputs=outputs),
        action,
    )


def _apply_positive(
    *, accepted: Mapping[str, Any], context: Mapping[str, Any], stage_root: Path, evidence_scope: str
) -> dict[str, Any]:
    request = accepted["request"]
    row = accepted["records"][0]
    parent_freeze_root = Path(context["freeze_root"])
    validate_task052_governed_freeze(parent_freeze_root)
    raw_repair = _publish_raw_repair(
        parent_freeze_root=parent_freeze_root,
        row=row,
        request=request,
        output_root=stage_root / "raw_repair",
    )
    repaired_freeze = _build_repaired_freeze(
        parent_freeze_root=parent_freeze_root,
        raw_repair=raw_repair,
        output_root=stage_root / "freeze",
    )
    matrix = StrictEngineeringPITMatrixBuilder(
        StrictEngineeringPITMatrixConfig(
            min_cross_section_breadth=int(context.get("min_cross_section_breadth", 30)),
            research_observable_cutoff=str(context["research_cutoff"]),
            target_endpoint_horizon_trade_days=2,
        )
    ).build(
        governed_freeze_dir=repaired_freeze.generation_dir,
        historical_universe_dir=context["universe_root"],
        output_root=stage_root / "matrix",
    )
    _assert_repair_in_matrix(Path(matrix.generation_dir), request, row)
    tensor = build_v3_tensor_generation(
        matrix_dir=matrix.generation_dir,
        feature_manifest_path=context["feature_manifest"],
        output_root=stage_root / "tensor",
    )
    materializations = _materialize_exact20(
        factors=context["factors"],
        freeze_root=Path(repaired_freeze.generation_dir),
        matrix_root=Path(matrix.generation_dir),
        tensor_root=Path(tensor["generation_dir"]),
        feature_manifest=Path(context["feature_manifest"]),
        promotion_policy=Path(context["promotion_policy"]),
        output_root=stage_root / "materializations",
        research_cutoff=str(context["research_cutoff"]),
    )
    truth = publish_truth_successor(
        parent_truth_manifest=context["truth_manifest"],
        api_name="daily",
        request=request,
        records=[row],
        response_evidence={
            "cache_sha256": accepted["acceptance"]["cache_sha256"],
            "transport_receipt_content_hash": accepted["transport_receipt"]["content_hash"],
        },
        output_root=stage_root / "truth",
        parent_apply_hash=accepted["acceptance"]["content_hash"],
        expected_record_count=context.get("expected_truth_record_count", 35844),
    )
    sentinel = _run_production_sentinel(
        context=context,
        freeze_root=Path(repaired_freeze.generation_dir),
        matrix_root=Path(matrix.generation_dir),
        tensor_root=Path(tensor["generation_dir"]),
        stage_root=stage_root / "firewall_sentinel",
        evidence_scope=evidence_scope,
    )
    replay = _run_native_replay(
        context=context,
        matrix_root=Path(matrix.generation_dir),
        materializations=materializations,
        truth_manifest=truth["manifest_path"],
        output_root=stage_root / "native_replay",
        evidence_scope=evidence_scope,
    )
    artifacts = [
        raw_repair["manifest_path"],
        repaired_freeze.manifest_path,
        matrix.manifest_path,
        tensor.get("manifest_path") or str(Path(tensor["generation_dir"]) / "task_053_v3_tensor_manifest.json"),
        truth["manifest_path"],
        sentinel["artifact_path"],
        replay["manifest_path"],
        stage_root / "raw_repair",
        Path(repaired_freeze.generation_dir),
        Path(matrix.generation_dir),
        Path(tensor["generation_dir"]),
        stage_root / "materializations",
        stage_root / "truth",
        stage_root / "firewall_sentinel",
        stage_root / "native_replay",
    ]
    return _stage_result(
        truth=truth,
        replay=replay,
        artifacts=artifacts,
        extra_outputs={
            "raw_repair": raw_repair["content_hash"],
            "freeze": repaired_freeze.content_hash,
            "matrix": matrix.content_hash,
            "tensor": tensor["content_hash"],
            "firewall_sentinel": sentinel["content_hash"],
            "exact20_materialization": canonical_hash([row["content_hash"] for row in materializations]),
        },
    )


def _apply_empty(
    *, accepted: Mapping[str, Any], context: Mapping[str, Any], stage_root: Path, evidence_scope: str
) -> dict[str, Any]:
    request = accepted["request"]
    truth = publish_truth_successor(
        parent_truth_manifest=context["truth_manifest"],
        api_name="daily",
        request=request,
        records=[],
        response_evidence={
            "cache_sha256": accepted["acceptance"]["cache_sha256"],
            "transport_receipt_content_hash": accepted["transport_receipt"]["content_hash"],
        },
        output_root=stage_root / "truth",
        parent_apply_hash=accepted["acceptance"]["content_hash"],
        expected_record_count=context.get("expected_truth_record_count", 35844),
    )
    replay = _run_native_replay(
        context=context,
        matrix_root=Path(context["matrix_root"]),
        materializations=context["parent_materializations"],
        truth_manifest=truth["manifest_path"],
        output_root=stage_root / "native_replay",
        evidence_scope=evidence_scope,
    )
    l2 = _publish_dynamic_l2(
        request=request,
        truth=truth,
        replay=replay,
        output_root=stage_root / "dynamic_l2",
    )
    return _stage_result(
        truth=truth,
        replay=replay,
        artifacts=[stage_root / "truth", stage_root / "native_replay", stage_root / "dynamic_l2"],
        extra_outputs={"dynamic_l2": l2["content_hash"]},
    )


def _run_production_sentinel(
    *, context: Mapping[str, Any], freeze_root: Path, matrix_root: Path, tensor_root: Path, stage_root: Path, evidence_scope: str
) -> dict[str, Any]:
    probe = stage_root / "probe_factor.json"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(json.dumps(asdict(context["factors"][0]), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    payload = run_task054b_production_sentinel(
        ProductionSentinelConfig(
            governed_freeze_dir=str(freeze_root),
            universe_dir=str(context["universe_root"]),
            published_matrix_dir=str(matrix_root),
            published_tensor_dir=str(tensor_root),
            feature_manifest_path=str(context["feature_manifest"]),
            probe_factor_path=str(probe),
            promotion_policy_path=str(context["promotion_policy"]),
            output_root=str(stage_root),
            research_end_date=str(context["research_cutoff"]),
            holdout_start_date=str(context["holdout_start_date"]),
            label_horizon=2,
            timeout_seconds=int(context.get("sentinel_timeout_seconds", 1800)),
        )
    )
    validate_task054b_production_sentinel(payload["artifact_path"], scheduler_state_dir=stage_root / "scheduler_state")
    if payload.get("status") != "passed" or payload.get("exact_run_count") != 12:
        raise Task055JApplicationError("task055j_production_sentinel_not_passed")
    return payload | {
        "input_evidence_scope": evidence_scope,
        "production_seal_eligible": evidence_scope == "real_production",
    }


def _run_native_replay(
    *,
    context: Mapping[str, Any],
    matrix_root: Path,
    materializations: Sequence[Mapping[str, Any]],
    truth_manifest: str | Path,
    output_root: Path,
    evidence_scope: str,
) -> dict[str, Any]:
    truth = validate_truth_v2(truth_manifest)
    successor_bundle = _successor_bundle(context, matrix_root, materializations)
    prepared = prepare_simulation_inputs(successor_bundle)
    dates = list(prepared["market"]["dates"])
    assets = list(prepared["market"]["assets"])
    matrix_marks = _matrix_marks(matrix_root, assets, dates)
    surface = build_valuation_surface(
        truth=truth,
        assets=assets,
        dates=dates,
        matrix=matrix_marks,
        corporate_actions=prepared["corporate_actions"],
    )
    projection = publish_valuation_projection(
        output_root=output_root / "valuation_projection",
        dates=dates,
        assets=assets,
        surface=surface,
        truth_v2_content_hash=truth["content_hash"],
        matrix_content_hash=read_json(matrix_root / "task_052a_strict_matrix_manifest.json")["content_hash"],
        builder_code_hash=canonical_hash([sha256_file(__file__), context["context_root"]]),
    )
    net_calculator = FeeProjectionCalculator(context["fee_schedule"], commission_mode="net_commission_3bp")
    producer = trace_causal_runs(
        {"manifest": {"exact20_ids": list(context["exact20_ids"])}},
        prepared,
        surface,
        net_calculator,
    )
    projection_loaded = load_valuation_projection(projection["manifest_path"])
    independent_prepared = _independent_successor_prepared(successor_bundle, projection_loaded)
    independent_net = independently_trace_prepared(
        bundle_manifest={"exact20_ids": list(context["exact20_ids"])},
        prepared=independent_prepared,
        projection=projection_loaded,
        calculator=FeeProjectionCalculator(context["fee_schedule"], commission_mode="net_commission_3bp"),
    )
    independent_all_in = independently_trace_prepared(
        bundle_manifest={"exact20_ids": list(context["exact20_ids"])},
        prepared=independent_prepared,
        projection=projection_loaded,
        calculator=FeeProjectionCalculator(context["fee_schedule"], commission_mode="all_in_commission_3bp"),
    )
    if (
        independent_net["run_rows_root"] != producer["run_rows_root"]
        or independent_net["held_mark_root"] != producer["held_mark_root"]
        or independent_net["frontier_root"] != producer["missing_key_root"]
    ):
        raise Task055JApplicationError("task055j_independent_native_replay_mismatch")
    union = sorted({tuple(item) for item in independent_net["frontier_keys"] + independent_all_in["frontier_keys"]})
    run_bytes = _jsonl_bytes(producer["run_rows"])
    held_bytes = _jsonl_bytes(producer["held_rows"])
    independent_payload = {
        "schema_version": "task055j_independent_causal_verification_v1",
        "status": "passed",
        "producer_run_rows_root": producer["run_rows_root"],
        "producer_held_mark_root": producer["held_mark_root"],
        "net_frontier_root": independent_net["frontier_root"],
        "all_in_frontier_root": independent_all_in["frontier_root"],
        "frontier_union": [list(item) for item in union],
        "frontier_union_root": canonical_hash(union),
        "net_terminal_counts": independent_net["terminal_counts"],
        "all_in_terminal_counts": independent_all_in["terminal_counts"],
    }
    independent_payload["content_hash"] = canonical_hash(independent_payload)
    independent_bytes = (json.dumps(independent_payload, indent=2, sort_keys=True) + "\n").encode()
    partitions = {
        "run_rows": _bytes_partition("causal_run_rows.jsonl", run_bytes),
        "held_marks": _bytes_partition("held_mark_ledger.jsonl", held_bytes),
        "independent_verification": _bytes_partition("independent_verification.json", independent_bytes),
    }
    semantic = {
        "schema_version": CAUSAL_REPLAY_SCHEMA,
        "status": "completed" if producer["terminal_counts"].get("causal_valuation_blocked", 0) == 0 else "domain_blocked",
        "evidence_scope": evidence_scope,
        "exact20_ids": list(context["exact20_ids"]),
        "scenarios": list(context["scenarios"]),
        "run_count": len(producer["run_rows"]),
        "terminal_counts": producer["terminal_counts"],
        "run_rows_root": producer["run_rows_root"],
        "held_mark_root": producer["held_mark_root"],
        "net_frontier_root": independent_net["frontier_root"],
        "all_in_frontier_root": independent_all_in["frontier_root"],
        "frontier_union": [list(item) for item in union],
        "frontier_union_root": canonical_hash(union),
        "truth_content_hash": truth["content_hash"],
        "matrix_content_hash": read_json(matrix_root / "task_052a_strict_matrix_manifest.json")["content_hash"],
        "simulation_bundle_parent_content_hash": context["simulation_bundle_content_hash"],
        "fee_schedule_content_hash": context["fee_schedule_content_hash"],
        "valuation_projection_content_hash": projection["content_hash"],
        "partitions": partitions,
    }
    result = publish_generation(
        output_root / "manifest",
        prefix="task055j_native_causal_replay",
        manifest_name="native_causal_replay.json",
        semantic=semantic,
        extra_files={
            "causal_run_rows.jsonl": run_bytes,
            "held_mark_ledger.jsonl": held_bytes,
            "independent_verification.json": independent_bytes,
        },
    )
    return validate_native_causal_replay(result["manifest_path"])


def _successor_bundle(
    context: Mapping[str, Any], matrix_root: Path, materializations: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    parent = load_simulation_bundle(context["simulation_bundle"])
    assets = list(map(str, parent["ts_codes"]))
    execution_dates = list(map(str, parent["execution_dates"]))
    signal_dates = list(map(str, parent["trade_dates"]))
    matrix_assets = _read_list(matrix_root / "ts_codes.json")
    matrix_dates = _read_list(matrix_root / "trade_dates.json")
    asset_positions = [matrix_assets.index(asset) for asset in assets]
    execution_positions = [matrix_dates.index(date) for date in execution_dates]
    signal_positions = [matrix_dates.index(date) for date in signal_dates]
    raw = {
        "open": _slice_matrix(matrix_root / "open.npy", asset_positions, execution_positions),
        "close": _slice_matrix(matrix_root / "close.npy", asset_positions, execution_positions),
        "vol": _slice_matrix(matrix_root / "volume.npy", asset_positions, execution_positions),
        "amount": _slice_matrix(matrix_root / "amount.npy", asset_positions, execution_positions),
    }
    validity = {
        "open": _slice_matrix(matrix_root / "open_validity.npy", asset_positions, execution_positions, dtype=bool),
        "close": _slice_matrix(matrix_root / "close_validity.npy", asset_positions, execution_positions, dtype=bool),
        "vol": _slice_matrix(matrix_root / "volume_validity.npy", asset_positions, execution_positions, dtype=bool),
        "amount": _slice_matrix(matrix_root / "amount_validity.npy", asset_positions, execution_positions, dtype=bool),
    }
    strict_masks = {
        Path(name).stem: _slice_matrix(matrix_root / name, asset_positions, signal_positions, dtype=bool)
        for name in SIGNAL_MASKS
    }
    execution_masks = {}
    for name in EXECUTION_MASKS:
        key = Path(name).stem
        candidate = matrix_root / name
        if candidate.is_file():
            execution_masks[key] = _slice_matrix(candidate, asset_positions, execution_positions, dtype=bool)
        else:
            execution_masks[key] = np.asarray(parent["execution_masks"][key], dtype=bool)
    execution_masks["corporate_action_validity"] = np.asarray(
        parent["execution_masks"]["corporate_action_validity"], dtype=bool
    )
    by_factor = {str(row["factor_id"]): row for row in materializations}
    factor_values = {}
    factor_validity = {}
    for factor_id in context["exact20_ids"]:
        entry = by_factor.get(factor_id)
        if entry is None:
            raise Task055JApplicationError(f"task055j_successor_materialization_missing:{factor_id}")
        factor_values[factor_id] = np.asarray(np.load(entry["values_path"], mmap_mode="r", allow_pickle=False))[
            np.ix_(asset_positions, signal_positions)
        ]
        factor_validity[factor_id] = np.asarray(np.load(entry["validity_path"], mmap_mode="r", allow_pickle=False))[
            np.ix_(asset_positions, signal_positions)
        ]
    return {
        "manifest": dict(parent["manifest"]),
        "trade_dates": signal_dates,
        "execution_dates": execution_dates,
        "ts_codes": assets,
        "factor_values": factor_values,
        "factor_validity": factor_validity,
        "strict_masks": strict_masks,
        "execution_masks": execution_masks,
        "execution_metadata": parent["execution_metadata"],
        "raw": raw,
        "raw_validity": validity,
        "benchmark_index_bars": parent["benchmark_index_bars"],
        "corporate_actions": parent["corporate_actions"],
        "unit_contract": parent["unit_contract"],
    }


def _independent_successor_prepared(successor_bundle: Mapping[str, Any], projection: Mapping[str, Any]) -> dict[str, Any]:
    from task_055_h.independent import _prepare

    return _prepare(successor_bundle, projection)


def _matrix_marks(matrix_root: Path, assets: list[str], dates: list[str]) -> dict[str, Any]:
    matrix_assets = _read_list(matrix_root / "ts_codes.json")
    matrix_dates = _read_list(matrix_root / "trade_dates.json")
    asset_positions = [matrix_assets.index(asset) for asset in assets]
    date_positions = [matrix_dates.index(date) for date in dates]
    return {
        "open": _slice_matrix(matrix_root / "open.npy", asset_positions, date_positions).T,
        "open_valid": _slice_matrix(matrix_root / "open_validity.npy", asset_positions, date_positions, dtype=bool).T,
        "close": _slice_matrix(matrix_root / "close.npy", asset_positions, date_positions).T,
        "close_valid": _slice_matrix(matrix_root / "close_validity.npy", asset_positions, date_positions, dtype=bool).T,
    }


def _production_context(seal: Mapping[str, Any]) -> dict[str, Any]:
    runtime = seal["runtime_authority"]
    governed = Path(seal["governed_root"])
    catalog = {row["role"]: row for row in runtime["application_artifacts"]["catalog"]}

    def resolve(role: str) -> Path:
        row = catalog[role]
        path = (governed / str(row["relative_path"])).resolve()
        if governed not in path.parents or path.is_symlink():
            raise Task055JApplicationError(f"task055j_production_context_escape:{role}")
        if row.get("sha256") and sha256_file(path) != row["sha256"]:
            raise Task055JApplicationError(f"task055j_production_context_sha_drift:{role}")
        return path

    exact_ids = list(runtime["application_artifacts"]["exact20_ids"])
    store_root = resolve("normalized_store_root")
    validate_normalized_replay_store(store_root, expected_ids=exact_ids)
    factors = LocalFactorStore(store_root).load_factors()
    simulation_bundle = resolve("simulation_bundle")
    bundle = validate_simulation_bundle(simulation_bundle, require_ready=True)
    fee = read_json(resolve("fee_schedule"))
    materializations = _materializations_from_bundle(simulation_bundle, exact_ids)
    context = {
        "freeze_root": str(resolve("freeze_manifest").parent),
        "universe_root": str(resolve("universe_manifest").parent),
        "matrix_root": str(resolve("matrix_root")),
        "tensor_root": str(resolve("tensor_root")),
        "feature_manifest": str(resolve("feature_manifest")),
        "promotion_policy": str(resolve("promotion_policy")),
        "truth_manifest": str(resolve("truth_v2")),
        "fee_schedule": str(resolve("fee_schedule")),
        "fee_schedule_content_hash": fee["content_hash"],
        "simulation_bundle": str(simulation_bundle),
        "simulation_bundle_content_hash": bundle["content_hash"],
        "factors": factors,
        "exact20_ids": exact_ids,
        "exact20_identity_root": runtime["application_artifacts"]["exact20_identity_root"],
        "parent_materializations": materializations,
        "research_cutoff": runtime["application_artifacts"]["research_cutoff"],
        "holdout_start_date": "20240531",
        "scenarios": ["baseline", "zero_cost_accounting", "double_modeled_cost", "participation_5_percent", "aum_10_million"],
        "expected_truth_record_count": 35844,
    }
    context["context_root"] = canonical_hash(
        {
            "application_tree_root": runtime["application_tree_root"],
            "exact20_identity_root": context["exact20_identity_root"],
            "truth": read_json(context["truth_manifest"])["content_hash"],
            "fee": context["fee_schedule_content_hash"],
            "bundle": context["simulation_bundle_content_hash"],
        }
    )
    return context


def _materializations_from_bundle(bundle_manifest: Path, exact_ids: list[str]) -> list[dict[str, Any]]:
    manifest = validate_simulation_bundle(bundle_manifest, require_ready=True)
    root = bundle_manifest.parent
    result = []
    for factor_id in exact_ids:
        values = root / manifest["artifacts"][f"factor:{factor_id}:values"]["path"]
        validity = root / manifest["artifacts"][f"factor:{factor_id}:validity"]["path"]
        result.append(
            {
                "factor_id": factor_id,
                "values_path": str(values),
                "validity_path": str(validity),
                "content_hash": canonical_hash([sha256_file(values), sha256_file(validity)]),
                "manifest_path": str(bundle_manifest),
            }
        )
    return result


def _publish_dynamic_l2(
    *, request: Mapping[str, Any], truth: Mapping[str, Any], replay: Mapping[str, Any], output_root: Path
) -> dict[str, Any]:
    from task_055_f.transport import evidence_use_identity, transport_identity

    fields = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
    params = {"ts_code": request["ts_code"], "trade_date": request["trade_date"]}
    transport_hash = transport_identity("suspend_d", params, fields)
    l2 = {
        "api_name": "suspend_d",
        "params": params,
        "fields": fields,
        "ts_code": request["ts_code"],
        "trade_date": request["trade_date"],
        "transport_hash": transport_hash,
        "evidence_use_hash": evidence_use_identity(
            stage="task055j_l2_exact",
            parent_plan_hash=truth["content_hash"],
            frontier_root=replay["frontier_union_root"],
            transport_hash=transport_hash,
        ),
    }
    return publish_generation(
        output_root,
        prefix="task055j_dynamic_l2",
        manifest_name="dynamic_l2_plan.json",
        semantic={
            "schema_version": "task055j_dynamic_exact_suspend_l2_v1",
            "status": "sealed_not_authorized",
            "parent_truth_content_hash": truth["content_hash"],
            "parent_replay_content_hash": replay["content_hash"],
            "requests": [l2],
            "request_count": 1,
            "network_executed": False,
            "resume_authorized": False,
            "application_support": "unsupported_waiting_for_separate_authority",
            "daily_empty_semantics": "vendor_absence_only_not_full_day_suspension_proof",
        },
    )


def _stage_result(
    *, truth: Mapping[str, Any], replay: Mapping[str, Any], artifacts: list[str], extra_outputs: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "stage_outputs": {
            **dict(extra_outputs),
            "truth": truth["content_hash"],
            "fee_aware_exact20_x5": replay["content_hash"],
            "next_frontier": replay["frontier_union_root"],
        },
        "truth_manifest": truth["manifest_path"],
        "replay_manifest": replay["manifest_path"],
        "terminal_pair_count": replay["run_count"],
        "terminal_counts": replay["terminal_counts"],
        "net_frontier_root": replay["net_frontier_root"],
        "all_in_frontier_root": replay["all_in_frontier_root"],
        "frontier_union_root": replay["frontier_union_root"],
        "frontier_union": replay["frontier_union"],
        "artifacts": artifacts,
    }


def _catalog(root: Path, paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    catalog = []
    seen: set[Path] = set()
    for raw in paths:
        path = Path(raw).resolve()
        candidates = [path] if path.is_file() else sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if root not in candidate.parents or candidate.is_symlink():
                raise Task055JApplicationError("task055j_application_catalog_escape")
            catalog.append(
                {
                    "path": candidate.relative_to(root).as_posix(),
                    "sha256": sha256_file(candidate),
                    "size_bytes": candidate.stat().st_size,
                }
            )
    return sorted(catalog, key=lambda row: row["path"])


def _slice_matrix(path: Path, asset_positions: list[int], date_positions: list[int], dtype: Any = None) -> np.ndarray:
    array = np.load(path, mmap_mode="r", allow_pickle=False)
    if array.ndim == 1:
        result = np.asarray(array[date_positions])
    elif array.ndim == 2:
        result = np.asarray(array[np.ix_(asset_positions, date_positions)])
    else:
        raise Task055JApplicationError(f"task055j_matrix_partition_rank_invalid:{path.name}:{array.ndim}")
    return result.astype(dtype, copy=False) if dtype is not None else result


def _read_list(path: Path) -> list[str]:
    return [str(value) for value in json.loads(path.read_text(encoding="utf-8"))]


def _current_manifest(root: Path, manifest_name: str) -> Path:
    pointer = read_json(root / "current.json")
    manifest = (root / str(pointer.get("manifest") or "")).resolve()
    if root.resolve() not in manifest.parents or manifest.name != manifest_name or not manifest.is_file():
        raise Task055JApplicationError(f"task055j_current_manifest_invalid:{root.name}:{manifest_name}")
    if pointer.get("content_hash") != read_json(manifest).get("content_hash"):
        raise Task055JApplicationError(f"task055j_current_manifest_hash_mismatch:{root.name}")
    return manifest


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n" for row in rows).encode()


def _bytes_partition(path: str, payload: bytes) -> dict[str, Any]:
    return {"path": path, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}


def stable_response_identity(accepted: Mapping[str, Any]) -> str:
    return canonical_hash(
        [
            accepted["transport_receipt"]["content_hash"],
            accepted["acceptance"]["cache_sha256"],
            len(accepted["records"]),
        ]
    )


def apply_synthetic_test_only(
    *, accepted: Mapping[str, Any], context: Mapping[str, Any], output_root: str | Path
) -> dict[str, Any]:
    if context.get("evidence_scope") != "synthetic_rehearsal_only":
        raise Task055JApplicationError("task055j_synthetic_context_scope_required")
    return _apply(
        accepted=accepted,
        context=context,
        output_root=Path(output_root),
        evidence_scope="synthetic_rehearsal_only",
    )
