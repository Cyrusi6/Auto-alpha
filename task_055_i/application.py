from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from data_lake.task052_freeze import (
    create_task052_governed_freeze,
    resolve_task052_governed_freeze_manifest,
    validate_task052_governed_freeze,
)
from factor_store.storage import LocalFactorStore
from matrix_store.strict_engineering import (
    StrictEngineeringPITMatrixBuilder,
    StrictEngineeringPITMatrixConfig,
)
from research_firewall import ResearchEligibilityContract
from task_053_a.orchestrator import build_v3_tensor_generation
from task_054_c.factor_store import validate_normalized_replay_store
from task_055_a.policy import PREREGISTERED_SCENARIOS
from task_055_a.simulator import EventLedgerSimulator, SimulationDataBlocker
from task_055_f.transport import evidence_use_identity, transport_identity
from task_055_h.fee import FeeProjectionCalculator
from task_055_h.io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation
from validation_lab.materialization import FactorMaterializer, MaterializationInputs

from .contracts import CANARY, MAX_DATE, RESPONSE_APPLICATION_SCHEMA
from .executor import (
    load_verified_canary_cache,
    load_verified_canary_cache_rehearsal,
)


class Task055IApplicationError(RuntimeError):
    pass


def apply_native_canary_response(
    *,
    runtime_authority: str | Path,
    canary_acceptance: str | Path,
) -> dict[str, Any]:
    verified = load_verified_canary_cache(
        runtime_authority=runtime_authority,
        canary_acceptance=canary_acceptance,
    )
    authority = verified["authority"]
    context = _production_context(authority)
    return _apply_verified_response(
        verified=verified,
        context=context,
        output_root=Path(authority["authority_root"]) / "applications",
        evidence_scope="real_production",
    )


def apply_rehearsal_canary_response(
    *,
    runtime_authority: str | Path,
    canary_acceptance: str | Path,
    context: Mapping[str, Any],
    output_root: str | Path,
) -> dict[str, Any]:
    verified = load_verified_canary_cache_rehearsal(
        runtime_authority=runtime_authority,
        canary_acceptance=canary_acceptance,
    )
    return _apply_verified_response(
        verified=verified,
        context=dict(context),
        output_root=Path(output_root),
        evidence_scope="synthetic_rehearsal_only",
    )


def apply_rehearsal_suspend_response(
    *,
    request: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    parent_application: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    parent = validate_native_response_application(parent_application)
    if parent.get("evidence_scope") != "synthetic_rehearsal_only":
        raise Task055IApplicationError("task055i_suspend_rehearsal_scope_invalid")
    action = _suspend_action(request, records)
    semantic = {
        "schema_version": RESPONSE_APPLICATION_SCHEMA,
        "status": "applied",
        "evidence_scope": "synthetic_rehearsal_only",
        "production_seal_eligible": False,
        "parent_application_content_hash": parent["content_hash"],
        "api_name": "suspend_d",
        "request": _request_summary(request),
        "response_item_count": len(records),
        "action": action,
        "stage_outputs": {
            "truth": canonical_hash([parent["content_hash"], action]),
            "fee_aware_exact20_x5": parent["stage_outputs"]["fee_aware_exact20_x5"],
            "next_frontier": canonical_hash([action["security_date"], action["outcome"]]),
        },
        "terminal_pair_count": 100,
        "certification_blocker_retained": True,
    }
    result = publish_generation(
        output_root,
        prefix="suspend_response_apply",
        manifest_name="response_application.json",
        semantic=semantic,
    )
    validate_native_response_application(result["manifest_path"])
    return result


def validate_native_response_application(path: str | Path) -> dict[str, Any]:
    payload = validate_generation(
        path,
        schema=RESPONSE_APPLICATION_SCHEMA,
        manifest_name="response_application.json",
    )
    if payload.get("status") != "applied":
        raise Task055IApplicationError("task055i_response_application_status_invalid")
    scope = payload.get("evidence_scope")
    if scope not in {"real_production", "synthetic_rehearsal_only"}:
        raise Task055IApplicationError("task055i_response_application_scope_invalid")
    if scope == "synthetic_rehearsal_only" and payload.get("production_seal_eligible") is not False:
        raise Task055IApplicationError("task055i_rehearsal_application_seal_boundary_invalid")
    if int(payload.get("terminal_pair_count") or 0) != 100:
        raise Task055IApplicationError("task055i_application_exact20_x5_invalid")
    outputs = payload.get("stage_outputs") or {}
    required = {"truth", "fee_aware_exact20_x5", "next_frontier"}
    if payload.get("api_name") == "daily" and payload.get("action", {}).get("kind") == "immutable_daily_raw_repair":
        required |= {"raw_repair", "freeze", "matrix", "tensor", "firewall_sentinel", "exact20_materialization"}
    if not required.issubset(outputs):
        raise Task055IApplicationError("task055i_application_stage_outputs_incomplete")
    artifact_catalog = list(payload.get("artifact_catalog") or ())
    manifest_path = Path(payload["manifest_path"])
    stage_root = manifest_path.parents[3]
    for row in artifact_catalog:
        relative = Path(str(row.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise Task055IApplicationError("task055i_application_artifact_path_invalid")
        artifact = (stage_root / relative).resolve()
        if stage_root.resolve() not in artifact.parents or not artifact.is_file() or artifact.is_symlink():
            raise Task055IApplicationError("task055i_application_artifact_missing")
        if sha256_file(artifact) != row.get("sha256"):
            raise Task055IApplicationError("task055i_application_artifact_sha_mismatch")
    return payload


def _apply_verified_response(
    *,
    verified: Mapping[str, Any],
    context: Mapping[str, Any],
    output_root: Path,
    evidence_scope: str,
) -> dict[str, Any]:
    request = dict(verified["request"])
    records = [dict(row) for row in verified["records"]]
    if request["api_name"] != "daily":
        raise Task055IApplicationError("task055i_canary_application_requires_daily")
    output_root.mkdir(parents=True, exist_ok=True)
    stage_root = output_root / f"staging_{request['transport_hash'][:16]}"
    stage_root.mkdir(parents=True, exist_ok=True)
    if records:
        action = _daily_action(request, records)
        stage = _apply_positive_daily(
            request=request,
            row=records[0],
            context=context,
            output_root=stage_root,
            evidence_scope=evidence_scope,
        )
    else:
        action = _daily_action(request, records)
        stage = _apply_empty_daily(
            request=request,
            context=context,
            output_root=stage_root,
            evidence_scope=evidence_scope,
        )
    response_rows = _jsonl(records)
    artifact_catalog = _catalog(output_root, stage["artifacts"])
    semantic = {
        "schema_version": RESPONSE_APPLICATION_SCHEMA,
        "status": "applied",
        "evidence_scope": evidence_scope,
        "production_seal_eligible": evidence_scope == "real_production",
        "runtime_authority_content_hash": verified["authority"]["content_hash"],
        "canary_acceptance_content_hash": verified["acceptance"]["content_hash"],
        "api_name": "daily",
        "request": _request_summary(request),
        "cache_sha256": verified["cache_sha256"],
        "response_item_count": len(records),
        "response_rows_sha256": canonical_hash(records),
        "action": action,
        "stage_outputs": stage["stage_outputs"],
        "artifact_catalog": artifact_catalog,
        "terminal_pair_count": 100,
        "terminal_counts": stage["terminal_counts"],
        "next_frontier_keys": stage["next_frontier_keys"],
        "next_frontier_root": canonical_hash(stage["next_frontier_keys"]),
        "candidate_reselection_allowed": False,
        "exact20_identity_root": context["exact20_identity_root"],
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
    }
    result = publish_generation(
        output_root / "published",
        prefix="native_response_application",
        manifest_name="response_application.json",
        semantic=semantic,
        extra_files={"verified_response_rows.jsonl": response_rows},
    )
    validate_native_response_application(result["manifest_path"])
    return result | {"stage_root": str(stage_root)}


def _apply_positive_daily(
    *,
    request: Mapping[str, Any],
    row: Mapping[str, Any],
    context: Mapping[str, Any],
    output_root: Path,
    evidence_scope: str,
) -> dict[str, Any]:
    parent_freeze_root = Path(context["freeze_root"])
    parent_freeze = validate_task052_governed_freeze(parent_freeze_root)
    raw_repair = _publish_raw_repair(
        parent_freeze_root=parent_freeze_root,
        row=row,
        request=request,
        output_root=output_root / "raw_repair",
    )
    repaired_freeze = _build_repaired_freeze(
        parent_freeze_root=parent_freeze_root,
        raw_repair=raw_repair,
        output_root=output_root / "freeze",
    )
    matrix = StrictEngineeringPITMatrixBuilder(
        StrictEngineeringPITMatrixConfig(
            min_cross_section_breadth=int(context.get("min_cross_section_breadth", 30)),
            research_observable_cutoff=str(context["research_cutoff"]),
        )
    ).build(
        governed_freeze_dir=repaired_freeze.generation_dir,
        historical_universe_dir=context["universe_root"],
        output_root=output_root / "matrix",
    )
    _assert_repair_in_matrix(Path(matrix.generation_dir), request, row)
    tensor = build_v3_tensor_generation(
        matrix_dir=matrix.generation_dir,
        feature_manifest_path=context["feature_manifest"],
        output_root=output_root / "tensor",
    )
    materializations = _materialize_exact20(
        factors=context["factors"],
        freeze_root=Path(repaired_freeze.generation_dir),
        matrix_root=Path(matrix.generation_dir),
        tensor_root=Path(tensor["generation_dir"]),
        feature_manifest=Path(context["feature_manifest"]),
        promotion_policy=Path(context["promotion_policy"]),
        output_root=output_root / "materializations",
        research_cutoff=str(context["research_cutoff"]),
    )
    sentinel = _run_firewall_sentinel(
        request=request,
        parent_matrix=Path(context["matrix_root"]),
        parent_tensor=Path(context["tensor_root"]),
        repaired_matrix=Path(matrix.generation_dir),
        repaired_tensor=Path(tensor["generation_dir"]),
        materializations=materializations,
        output_root=output_root / "firewall_sentinel",
    )
    replay = _run_exact20_x5(
        factors=context["factors"],
        materializations=materializations,
        matrix_root=Path(matrix.generation_dir),
        fee_calculator=_fee_calculator(context),
        output_root=output_root / "fee_aware_replay",
        evidence_scope=evidence_scope,
    )
    truth = publish_generation(
        output_root / "truth",
        prefix="truth_after_positive_daily",
        manifest_name="truth_generation.json",
        semantic={
            "schema_version": "task055i_truth_generation_v1",
            "status": "rebuilt",
            "security_date": [request["ts_code"], request["trade_date"]],
            "classification": "TRADED_PRIMARY_BAR",
            "row_hash": canonical_hash(row),
            "raw_repair_content_hash": raw_repair["content_hash"],
            "matrix_content_hash": matrix.content_hash,
        },
    )
    artifacts = [
        raw_repair["manifest_path"],
        repaired_freeze.manifest_path,
        matrix.manifest_path,
        tensor["manifest_path"] if "manifest_path" in tensor else str(Path(tensor["generation_dir"]) / "task_053_v3_tensor_manifest.json"),
        sentinel["manifest_path"],
        replay["manifest_path"],
        truth["manifest_path"],
        *[item["manifest_path"] for item in materializations],
        *replay["artifact_paths"],
    ]
    return {
        "stage_outputs": {
            "raw_repair": raw_repair["content_hash"],
            "freeze": repaired_freeze.content_hash,
            "matrix": matrix.content_hash,
            "tensor": tensor["content_hash"],
            "firewall_sentinel": sentinel["content_hash"],
            "exact20_materialization": canonical_hash([item["content_hash"] for item in materializations]),
            "truth": truth["content_hash"],
            "fee_aware_exact20_x5": replay["content_hash"],
            "next_frontier": replay["next_frontier_root"],
        },
        "terminal_counts": replay["terminal_counts"],
        "next_frontier_keys": replay["next_frontier_keys"],
        "artifacts": artifacts,
    }


def _apply_empty_daily(
    *,
    request: Mapping[str, Any],
    context: Mapping[str, Any],
    output_root: Path,
    evidence_scope: str,
) -> dict[str, Any]:
    truth = publish_generation(
        output_root / "truth",
        prefix="truth_after_empty_daily",
        manifest_name="truth_generation.json",
        semantic={
            "schema_version": "task055i_truth_generation_v1",
            "status": "rebuilt",
            "security_date": [request["ts_code"], request["trade_date"]],
            "classification": "VENDOR_DAILY_ABSENCE_NOT_NO_TRADE_PROOF",
            "proves_suspension": False,
            "parent_truth_content_hash": context.get("truth_content_hash"),
        },
    )
    replay = _run_exact20_x5(
        factors=context["factors"],
        materializations=context["parent_materializations"],
        matrix_root=Path(context["matrix_root"]),
        fee_calculator=_fee_calculator(context),
        output_root=output_root / "fee_aware_replay",
        evidence_scope=evidence_scope,
    )
    frontier = sorted({*map(tuple, replay["next_frontier_keys"]), (request["ts_code"], request["trade_date"])})
    fields = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
    params = {"ts_code": request["ts_code"], "trade_date": request["trade_date"]}
    transport = transport_identity("suspend_d", params, fields)
    l2 = {
        "api_name": "suspend_d",
        "params": params,
        "fields": fields,
        "ts_code": request["ts_code"],
        "trade_date": request["trade_date"],
        "transport_hash": transport,
        "evidence_use_hash": evidence_use_identity(
            stage="task055i_dynamic_l2_exact",
            parent_plan_hash=truth["content_hash"],
            frontier_root=canonical_hash(frontier),
            transport_hash=transport,
        ),
    }
    plan = publish_generation(
        output_root / "dynamic_l2",
        prefix="dynamic_exact_suspend_l2",
        manifest_name="dynamic_l2_plan.json",
        semantic={
            "schema_version": "task055i_dynamic_l2_plan_v1",
            "status": "sealed_not_authorized",
            "parent_truth_content_hash": truth["content_hash"],
            "requests": [l2],
            "resume_authorized": False,
            "network_executed": False,
            "daily_empty_semantics": "vendor_absence_only",
        },
    )
    return {
        "stage_outputs": {
            "truth": truth["content_hash"],
            "fee_aware_exact20_x5": replay["content_hash"],
            "next_frontier": canonical_hash(frontier),
            "dynamic_l2": plan["content_hash"],
        },
        "terminal_counts": replay["terminal_counts"],
        "next_frontier_keys": [list(item) for item in frontier],
        "artifacts": [
            truth["manifest_path"],
            replay["manifest_path"],
            plan["manifest_path"],
            *replay["artifact_paths"],
        ],
    }


def _publish_raw_repair(
    *,
    parent_freeze_root: Path,
    row: Mapping[str, Any],
    request: Mapping[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    manifest = read_json(resolve_task052_governed_freeze_manifest(parent_freeze_root))
    bars = parent_freeze_root / str((manifest["artifacts_by_name"]["daily_bars"])["relative_path"])
    output_root.mkdir(parents=True, exist_ok=True)
    merged = output_root / "daily_bars_repaired.jsonl"
    found = False
    temporary = merged.with_suffix(".tmp")
    with bars.open("r", encoding="utf-8") as source, temporary.open("w", encoding="utf-8") as target:
        for line in source:
            current = json.loads(line)
            if current.get("ts_code") == request["ts_code"] and current.get("trade_date") == request["trade_date"]:
                if found:
                    raise Task055IApplicationError("task055i_parent_daily_duplicate_key")
                current = dict(row)
                found = True
            target.write(json.dumps(current, sort_keys=True, separators=(",", ":")) + "\n")
        if not found:
            target.write(json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n")
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, merged)
    semantic = {
        "schema_version": "task055i_raw_repair_v1",
        "status": "published",
        "parent_freeze_content_hash": manifest["content_hash"],
        "security_date": [request["ts_code"], request["trade_date"]],
        "row_hash": canonical_hash(row),
        "replaced_existing_row": found,
        "merged_daily_bars_sha256": sha256_file(merged),
        "source_transport_hash": request["transport_hash"],
    }
    return publish_generation(
        output_root / "manifest",
        prefix="raw_repair",
        manifest_name="raw_repair.json",
        semantic=semantic,
    ) | {"merged_daily_bars_path": str(merged)}


def _build_repaired_freeze(
    *,
    parent_freeze_root: Path,
    raw_repair: Mapping[str, Any],
    output_root: Path,
):
    manifest_path = resolve_task052_governed_freeze_manifest(parent_freeze_root)
    manifest = read_json(manifest_path)
    artifacts = {
        str(item["logical_name"]): parent_freeze_root / str(item["relative_path"])
        for item in manifest["artifacts"]
    }
    artifacts["daily_bars"] = Path(raw_repair["merged_daily_bars_path"])
    lineage = output_root / "source_lineage.json"
    lineage.parent.mkdir(parents=True, exist_ok=True)
    lineage.write_text(
        json.dumps(
            {
                "parent_freeze_content_hash": manifest["content_hash"],
                "raw_repair_content_hash": raw_repair["content_hash"],
                "raw_repair_manifest_sha256": sha256_file(raw_repair["manifest_path"]),
            },
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return create_task052_governed_freeze(
        artifacts,
        output_root / "generations",
        source_lineage_manifest_path=lineage,
    )


def _materialize_exact20(
    *,
    factors: Sequence[Any],
    freeze_root: Path,
    matrix_root: Path,
    tensor_root: Path,
    feature_manifest: Path,
    promotion_policy: Path,
    output_root: Path,
    research_cutoff: str,
) -> list[dict[str, Any]]:
    tensor_manifest = read_json(tensor_root / "task_053_v3_tensor_manifest.json")
    matrix_manifest = read_json(matrix_root / "task_052a_strict_matrix_manifest.json")
    materializer = FactorMaterializer(
        MaterializationInputs(
            data_freeze_dir=str(freeze_root),
            matrix_cache_dir=str(matrix_root),
            feature_manifest_path=str(feature_manifest),
            feature_tensor_path=str(tensor_root / "feature_tensor.npy"),
            feature_validity_tensor_path=str(tensor_root / "feature_validity_tensor.npy"),
            promotion_policy_path=str(promotion_policy),
            target_return_mode="target_open_t1_t2",
            feature_cutoff_mode="next_trade_day_open",
            research_end_date=research_cutoff,
            label_horizon=2,
            research_eligible_date_mask_path=str(matrix_root / "research_eligible_date_mask.npy"),
            eligibility_contract_hash=str(matrix_manifest["eligible_date_hash"]),
            research_computation_identity=canonical_hash(
                [matrix_manifest["eligible_date_hash"], tensor_manifest["content_hash"], research_cutoff]
            ),
        ),
        output_root,
        device="cpu",
        min_coverage=0.0001,
        max_coverage=1.0,
    )
    results = []
    for factor in factors:
        result = materializer.materialize(factor)
        if result.status != "success" or result.cache_hit:
            raise Task055IApplicationError(f"task055i_exact20_materialization_failed:{factor.factor_id}:{result.blocker}")
        payload = read_json(result.manifest_path)
        results.append(
            {
                "factor_id": factor.factor_id,
                "content_hash": canonical_hash(
                    [payload["input_fingerprint"], payload["value_sha256"], payload["validity_sha256"]]
                ),
                "manifest_path": result.manifest_path,
                "values_path": result.values_path,
                "validity_path": result.validity_path,
            }
        )
    if len(results) != 20 or len({row["factor_id"] for row in results}) != 20:
        raise Task055IApplicationError("task055i_materialization_exact20_identity_invalid")
    return results


def _run_firewall_sentinel(
    *,
    request: Mapping[str, Any],
    parent_matrix: Path,
    parent_tensor: Path,
    repaired_matrix: Path,
    repaired_tensor: Path,
    materializations: Sequence[Mapping[str, Any]],
    output_root: Path,
) -> dict[str, Any]:
    dates = _read_list(repaired_matrix / "trade_dates.json")
    contract = ResearchEligibilityContract("20240530", 2)
    eligible = contract.eligible_dates(dates)
    if request["trade_date"] not in dates or request["trade_date"] > "20240530":
        raise Task055IApplicationError("task055i_repair_not_inside_research_axis")
    parent_tensor_manifest = read_json(parent_tensor / "task_053_v3_tensor_manifest.json")
    repaired_tensor_manifest = read_json(repaired_tensor / "task_053_v3_tensor_manifest.json")
    if parent_tensor_manifest["content_hash"] == repaired_tensor_manifest["content_hash"]:
        raise Task055IApplicationError("task055i_inside_cutoff_tensor_change_missing")
    factor_root = canonical_hash([row["content_hash"] for row in materializations])
    semantic = {
        "schema_version": "task055i_response_firewall_sentinel_v1",
        "status": "passed",
        "mutation_security_date": [request["ts_code"], request["trade_date"]],
        "research_cutoff": "20240530",
        "eligible_date_hash": contract.eligible_date_hash(dates),
        "eligible_date_count": len(eligible),
        "parent_matrix_content_hash": read_json(parent_matrix / "task_052a_strict_matrix_manifest.json")["content_hash"],
        "repaired_matrix_content_hash": read_json(repaired_matrix / "task_052a_strict_matrix_manifest.json")["content_hash"],
        "parent_tensor_content_hash": parent_tensor_manifest["content_hash"],
        "repaired_tensor_content_hash": repaired_tensor_manifest["content_hash"],
        "inside_cutoff_cache_miss": True,
        "research_semantic_change": True,
        "exact20_materialization_root": factor_root,
        "post_cutoff_access_count": 0,
    }
    return publish_generation(
        output_root,
        prefix="response_firewall_sentinel",
        manifest_name="firewall_sentinel.json",
        semantic=semantic,
    )


def _run_exact20_x5(
    *,
    factors: Sequence[Any],
    materializations: Sequence[Mapping[str, Any]],
    matrix_root: Path,
    fee_calculator: Any,
    output_root: Path,
    evidence_scope: str,
) -> dict[str, Any]:
    by_factor = {str(row["factor_id"]): row for row in materializations}
    dates = _read_list(matrix_root / "trade_dates.json")
    assets = _read_list(matrix_root / "ts_codes.json")
    full_shape = (len(assets), len(dates))
    matrix_manifest = read_json(matrix_root / "task_052a_strict_matrix_manifest.json")
    cutoff = str(matrix_manifest.get("research_end_date") or "20240530")
    date_count = sum(date <= cutoff for date in dates)
    if date_count < 3:
        raise Task055IApplicationError("task055i_replay_research_axis_too_short")
    dates = dates[:date_count]
    raw_open = _load(matrix_root / "open.npy", full_shape).T[:date_count]
    raw_close = _load(matrix_root / "close.npy", full_shape).T[:date_count]
    volume = _load(matrix_root / "volume.npy", full_shape).T[:date_count]
    volume_valid = _load(matrix_root / "volume_validity.npy", full_shape, bool).T[:date_count]
    adv = _lagged_adv(volume, volume_valid)
    buy = _load(matrix_root / "buyable_at_open.npy", full_shape, bool).T[:date_count]
    sell = _load(matrix_root / "sellable_at_open.npy", full_shape, bool).T[:date_count]
    signal = _load(matrix_root / "signal_candidate_cells.npy", full_shape, bool).T[:date_count]
    open_valid = _load(matrix_root / "open_validity.npy", full_shape, bool).T[:date_count]
    close_valid = _load(matrix_root / "close_validity.npy", full_shape, bool).T[:date_count]
    valuation_open = np.where(open_valid, raw_open, np.nan)
    valuation_close = np.where(close_valid, raw_close, np.nan)
    evidence_open = np.where(open_valid, "raw_open", "").astype(object)
    evidence_close = np.where(close_valid, "raw_close", "").astype(object)
    date_matrix = np.broadcast_to(np.asarray(dates, dtype=object)[:, None], raw_open.shape)
    market = {
        "dates": dates,
        "assets": assets,
        "open": raw_open,
        "close": raw_close,
        "adv": adv,
        "valuation_open": valuation_open,
        "valuation_close": valuation_close,
        "valuation_open_method": np.where(open_valid, "OFFICIAL_OPEN", ""),
        "valuation_close_method": np.where(close_valid, "OFFICIAL_CLOSE", ""),
        "valuation_open_source_date": np.where(open_valid, date_matrix, ""),
        "valuation_close_source_date": np.where(close_valid, date_matrix, ""),
        "valuation_open_stale_age": np.where(open_valid, 0, -1).astype(np.int32),
        "valuation_close_stale_age": np.where(close_valid, 0, -1).astype(np.int32),
        "valuation_open_evidence_id": evidence_open,
        "valuation_close_evidence_id": evidence_close,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    run_rows = []
    artifact_paths = []
    frontier: set[tuple[str, str]] = set()
    terminal_counts: dict[str, int] = {}
    for factor in factors:
        entry = by_factor.get(factor.factor_id)
        if entry is None:
            raise Task055IApplicationError(f"task055i_replay_materialization_missing:{factor.factor_id}")
        values = np.load(entry["values_path"], allow_pickle=False).T[:date_count]
        validity = np.load(entry["validity_path"], allow_pickle=False).T[:date_count]
        for scenario_name, policy in PREREGISTERED_SCENARIOS.items():
            marks: list[dict[str, Any]] = []
            simulator = EventLedgerSimulator(
                policy,
                fee_calculator=fee_calculator,
                require_external_fee_schedule=True,
                require_explicit_valuation_marks=True,
            )
            terminal = "completed"
            blocker = None
            try:
                result = simulator.run(
                    market,
                    values,
                    masks={"buy": buy, "sell": sell, "select": signal & validity},
                    diagnostic_mark_observer_v2=lambda _i, _d, _p, rows: marks.extend(dict(row) for row in rows),
                )
                payload = result.to_dict()
            except SimulationDataBlocker as exc:
                terminal = "data_blocked"
                blocker = str(exc)
                payload = {"orders": [], "fills": [], "rejections": [], "settlements": [], "nav": [], "event_ledger": []}
                key = _blocker_key(blocker)
                if key is not None:
                    frontier.add(key)
            run_dir = output_root / "runs" / factor.factor_id / scenario_name
            run_dir.mkdir(parents=True, exist_ok=True)
            run_payload = {
                "factor_id": factor.factor_id,
                "scenario": scenario_name,
                "terminal_state": terminal,
                "blocker": blocker,
                "orders": payload["orders"],
                "fills": payload["fills"],
                "rejections": payload["rejections"],
                "settlements": payload["settlements"],
                "nav": payload["nav"],
                "event_ledger": payload["event_ledger"],
                "held_marks": marks,
            }
            run_path = run_dir / "run.json"
            run_path.write_text(json.dumps(run_payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            run_hash = sha256_file(run_path)
            artifact_paths.append(str(run_path))
            run_rows.append({
                "factor_id": factor.factor_id,
                "scenario": scenario_name,
                "terminal_state": terminal,
                "blocker": blocker,
                "run_sha256": run_hash,
            })
            terminal_counts[terminal] = terminal_counts.get(terminal, 0) + 1
    expected = sorted((factor.factor_id, scenario) for factor in factors for scenario in PREREGISTERED_SCENARIOS)
    actual = sorted((row["factor_id"], row["scenario"]) for row in run_rows)
    if len(factors) != 20 or actual != expected or len(actual) != len(set(actual)):
        raise Task055IApplicationError("task055i_replay_exact20_x5_cartesian_invalid")
    next_frontier = [list(item) for item in sorted(frontier)]
    semantic = {
        "schema_version": "task055i_fee_aware_exact20_x5_replay_v1",
        "status": "completed" if terminal_counts.get("data_blocked", 0) == 0 else "domain_blocked",
        "evidence_scope": evidence_scope,
        "run_count": 100,
        "terminal_counts": terminal_counts,
        "run_root": canonical_hash(run_rows),
        "next_frontier_keys": next_frontier,
        "next_frontier_root": canonical_hash(next_frontier),
        "candidate_reselection_allowed": False,
    }
    result = publish_generation(
        output_root / "manifest",
        prefix="fee_aware_exact20_x5",
        manifest_name="fee_aware_replay.json",
        semantic=semantic,
    )
    return result | {"next_frontier_keys": next_frontier, "next_frontier_root": semantic["next_frontier_root"], "terminal_counts": terminal_counts, "artifact_paths": artifact_paths}


def _production_context(authority: Mapping[str, Any]) -> dict[str, Any]:
    governed = Path(authority["governed_root"])
    catalog = {row["role"]: row for row in authority["application_artifacts"]["catalog"]}

    def path(role: str) -> Path:
        row = catalog[role]
        candidate = (governed / row["relative_path"]).resolve()
        if governed not in candidate.parents or candidate.is_symlink():
            raise Task055IApplicationError(f"task055i_application_context_escape:{role}")
        if row.get("sha256") and sha256_file(candidate) != row["sha256"]:
            raise Task055IApplicationError(f"task055i_application_context_sha_drift:{role}")
        if row.get("root_identity"):
            metadata = candidate.stat()
            if canonical_hash([str(candidate), metadata.st_dev, metadata.st_ino]) != row["root_identity"]:
                raise Task055IApplicationError(f"task055i_application_context_root_drift:{role}")
        return candidate

    normalized = path("normalized_store_root")
    exact_ids = list(authority["application_artifacts"]["exact20_ids"])
    validate_normalized_replay_store(normalized, expected_ids=exact_ids)
    factors = LocalFactorStore(normalized).load_factors()
    tensor_root = path("tensor_root")
    parent_materializations = _existing_or_materialize_parent(
        factors=factors,
        freeze_root=path("freeze_manifest").parent,
        matrix_root=path("matrix_root"),
        tensor_root=tensor_root,
        feature_manifest=path("feature_manifest"),
        promotion_policy=path("promotion_policy"),
        output_root=Path(authority["authority_root"]) / "applications" / "parent_materializations",
    )
    return {
        "freeze_root": str(path("freeze_manifest").parent),
        "universe_root": str(path("universe_manifest").parent),
        "matrix_root": str(path("matrix_root")),
        "tensor_root": str(tensor_root),
        "feature_manifest": str(path("feature_manifest")),
        "promotion_policy": str(path("promotion_policy")),
        "fee_schedule": str(path("fee_schedule")),
        "truth_content_hash": read_json(path("truth_v2"))["content_hash"],
        "exact20_identity_root": authority["application_artifacts"]["exact20_identity_root"],
        "factors": factors,
        "parent_materializations": parent_materializations,
        "research_cutoff": authority["application_artifacts"]["research_cutoff"],
    }


def _existing_or_materialize_parent(**kwargs: Any) -> list[dict[str, Any]]:
    return _materialize_exact20(research_cutoff="20240530", **kwargs)


def _fee_calculator(context: Mapping[str, Any]) -> Any:
    if context.get("fee_calculator") is not None:
        return context["fee_calculator"]
    return FeeProjectionCalculator(context["fee_schedule"], commission_mode="net_commission_3bp")


def _daily_action(request: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    key = {"ts_code": request["ts_code"], "trade_date": request["trade_date"]}
    if not records:
        return {
            "kind": "vendor_daily_absence",
            "security_date": key,
            "proves_suspension": False,
            "next_stage": "truth_rebuild_then_dynamic_exact_suspend_l2",
        }
    if len(records) != 1:
        raise Task055IApplicationError("task055i_exact_daily_cardinality_invalid")
    return {
        "kind": "immutable_daily_raw_repair",
        "security_date": key,
        "row_hash": canonical_hash(records[0]),
        "inside_research_cutoff": request["trade_date"] <= "20240530",
    }


def _suspend_action(request: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    types = sorted({str(row.get("suspend_type")) for row in records})
    timings = sorted("<null>" if row.get("suspend_timing") is None else str(row.get("suspend_timing")) for row in records)
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
        "security_date": {"ts_code": request["ts_code"], "trade_date": request["trade_date"]},
        "suspend_types": types,
        "timing_values": timings,
        "outcome": outcome,
        "certification_blocker_retained": True,
    }


def _assert_repair_in_matrix(matrix_root: Path, request: Mapping[str, Any], row: Mapping[str, Any]) -> None:
    codes = _read_list(matrix_root / "ts_codes.json")
    dates = _read_list(matrix_root / "trade_dates.json")
    stock = codes.index(request["ts_code"])
    date = dates.index(request["trade_date"])
    values = np.load(matrix_root / "open.npy", allow_pickle=False)
    validity = np.load(matrix_root / "open_validity.npy", allow_pickle=False)
    if not validity[stock, date] or not math.isclose(float(values[stock, date]), float(row["open"]), rel_tol=0.0, abs_tol=1e-6):
        raise Task055IApplicationError("task055i_raw_repair_not_present_in_matrix")


def _catalog(root: Path, paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    result = []
    for raw in paths:
        path = Path(raw).resolve()
        if root.resolve() not in path.parents or not path.is_file() or path.is_symlink():
            raise Task055IApplicationError("task055i_stage_artifact_outside_application_root")
        result.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        })
    return sorted(result, key=lambda row: row["path"])


def _request_summary(request: Mapping[str, Any]) -> dict[str, Any]:
    return {key: request[key] for key in ("api_name", "ts_code", "trade_date", "fields", "transport_hash", "evidence_use_hash")}


def _read_list(path: Path) -> list[str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise Task055IApplicationError(f"task055i_axis_not_list:{path.name}")
    return [str(item) for item in value]


def _load(path: Path, shape: tuple[int, int], dtype: Any = float) -> np.ndarray:
    value = np.asarray(np.load(path, allow_pickle=False), dtype=dtype)
    if value.shape != shape:
        raise Task055IApplicationError(f"task055i_matrix_shape_mismatch:{path.name}")
    return value


def _lagged_adv(volume: np.ndarray, validity: np.ndarray) -> np.ndarray:
    observed = np.where(validity & np.isfinite(volume) & (volume >= 0), volume, np.nan)
    result = np.zeros_like(observed, dtype=float)
    for index in range(1, len(observed)):
        start = max(0, index - 20)
        history = observed[start:index]
        count = np.sum(np.isfinite(history), axis=0)
        total = np.nansum(history, axis=0)
        result[index] = np.divide(total, count, out=np.zeros_like(total), where=count > 0)
    return result


def _blocker_key(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) >= 4 and len(parts[1]) == 8:
        return parts[2], parts[1]
    return None


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join((json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n").encode() for row in rows)
