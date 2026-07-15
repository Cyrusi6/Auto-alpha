"""Production Task 055-B orchestration and fail-closed final evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from task_055_a.bundle import validate_simulation_bundle
from task_055_a.observation import validate_observation_boundary_seal
from task_055_a.run import PHYSICAL_STATE_NAMES, inspect_physical_states

from .evidence import canonical_hash, sha256_file, validate_evidence_overlay
from .fees import validate_fee_schedule
from .inventory import validate_gap_inventory
from .preflight import validate_preflight_report
from .request_plan import validate_evidence_run, validate_request_plan
from .valuation import validate_valuation_overlay

FINAL_REPORT_SCHEMA = "task055b_final_report_v1"
FINAL_POINTER_SCHEMA = "task055b_final_report_pointer_v1"
BLOCKED_STATUS = "task055b_security_date_evidence_remediation_blocked"
COMPLETED_STATUS = (
    "task055b_security_date_evidence_closed_simulator_engineering_completed_"
    "historical_selection_contaminated_execution_modeled_future_holdout_waiting_"
    "certification_blocked"
)


class Task055BOrchestrationError(RuntimeError):
    """Raised when native evidence cannot close the Task 055-B lineage."""


def run_task055b(config: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "observation_seal",
        "task055a_simulation_bundle",
        "inventory_manifest",
        "request_plan_manifest",
        "evidence_overlay",
        "valuation_overlay",
        "valuation_preflight",
        "physical_state_roots",
        "output_root",
    }
    missing = sorted(required - set(config))
    if missing:
        raise Task055BOrchestrationError(f"task055b_config_missing:{missing}")

    seal = validate_observation_boundary_seal(config["observation_seal"], rescan=True)
    parent_bundle = validate_simulation_bundle(config["task055a_simulation_bundle"], require_ready=True)
    inventory = validate_gap_inventory(config["inventory_manifest"])
    request_plan = validate_request_plan(config["request_plan_manifest"])
    request_execution = validate_evidence_run(
        config["request_execution_manifest"], request_plan=config["request_plan_manifest"]
    ) if config.get("request_execution_manifest") else None
    evidence = validate_evidence_overlay(config["evidence_overlay"])
    valuation = validate_valuation_overlay(config["valuation_overlay"], evidence_overlay=config["evidence_overlay"])
    preflight = validate_preflight_report(config["valuation_preflight"])
    physical = inspect_physical_states(config["physical_state_roots"])
    fee_schedule = validate_fee_schedule(config["fee_schedule"]) if config.get("fee_schedule") else None

    _validate_lineage(inventory, request_plan, evidence, valuation, preflight)
    nonempty = {name: row["record_count"] for name, row in physical.items() if row["record_count"]}
    if nonempty:
        raise Task055BOrchestrationError(f"task055b_downstream_state_nonempty:{nonempty}")

    closure_ready = preflight["status"] == "passed" and all(
        bool(preflight["readiness"].get(name))
        for name in ("continuous_portfolio_valuation_ready", "future_research_data_ready")
    )
    if config.get("simulation_replay_evidence") is not None:
        raise Task055BOrchestrationError("task055b_injected_simulation_replay_evidence_forbidden")
    runs = _verify_physical_replay_tree(config.get("simulation_run_root"))
    replay_complete = bool(runs.get("verified"))
    if replay_complete and not closure_ready:
        raise Task055BOrchestrationError("task055b_replay_evidence_present_before_closure_gate")

    status = COMPLETED_STATUS if closure_ready and replay_complete else BLOCKED_STATUS
    blockers = _blockers(inventory, request_plan, evidence, preflight, fee_schedule, replay_complete)
    report = {
        "schema_version": FINAL_REPORT_SCHEMA,
        "status": status,
        "historical_selection_contaminated": True,
        "execution_evidence_level": "modeled_daily_bar_proxy",
        "prospective_holdout_opened": False,
        "parent_observation_seal_content_hash": seal.get("content_hash"),
        "parent_task055a_bundle_content_hash": parent_bundle.get("content_hash"),
        "inventory": _summary(inventory, ("content_hash", "cell_count", "episode_count", "first_blocker_count", "first_blocker_semantics", "state_counts", "probe_results", "readiness")),
        "request_plan": _summary(request_plan, ("content_hash", "gap_cell_count", "request_count", "unique_gap_dates", "affected_ts_codes", "max_network_requests")),
        "network_execution": {
            "performed": bool(request_execution and int(request_execution.get("network_request_count", 0))),
            "status": request_execution.get("status") if request_execution else "not_executed",
            "reason": "bounded_plan_exceeds_governed_small_repair_gate",
            "planned_request_count": int(request_plan.get("request_count", 0)),
            "completed_request_count": int(request_execution.get("completed_request_count", 0)) if request_execution else 0,
            "network_request_count": int(request_execution.get("network_request_count", 0)) if request_execution else 0,
            "cache_exact_fingerprint_hits": int(request_execution.get("cache_hit_count", 0)) if request_execution else int(config.get("cache_exact_fingerprint_hits", 0)),
            "evidence_content_hash": request_execution.get("content_hash") if request_execution else None,
        },
        "evidence_overlay": _summary(evidence, ("content_hash", "record_count", "state_counts", "review_version")),
        "valuation_overlay": _summary(valuation, ("content_hash", "record_count", "state_counts")),
        "valuation_preflight": _summary(preflight, ("content_hash", "status", "readiness", "metrics")),
        "fee_schedule": _summary(fee_schedule, ("content_hash", "policy_id", "governed_fee_types", "modeled_fee_types")) if fee_schedule else None,
        "simulation_replay": runs,
        "readiness": {
            "factor_replay_ready": bool(preflight["readiness"].get("factor_replay_ready")),
            "continuous_portfolio_valuation_ready": bool(preflight["readiness"].get("continuous_portfolio_valuation_ready")),
            "future_research_data_ready": bool(preflight["readiness"].get("future_research_data_ready")),
            "certification_ready": False,
            "portfolio_ready": False,
            "paper_ready": False,
            "live_ready": False,
        },
        "physical_state_inventory": physical,
        "queues": {name: int(physical[name]["record_count"]) for name in PHYSICAL_STATE_NAMES},
        "blockers": blockers,
        "certification_blockers": [
            "minute_and_auction_data_unavailable",
            "order_book_queue_unavailable",
            "market_impact_uncalibrated",
            "broker_specific_commission_unproven",
            "pit_barra_unavailable",
            "suspension_timing_semantics_uncertified",
            "constituent_publication_timing_unknown",
            "vendor_historical_revision_risk",
            "historical_selection_contaminated",
            "prospective_holdout_not_arrived",
        ],
    }
    return _publish(Path(config["output_root"]), report)


def validate_task055b_final_report(path: str | Path) -> dict[str, Any]:
    report_path = Path(path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != FINAL_REPORT_SCHEMA:
        raise Task055BOrchestrationError("task055b_final_report_schema_invalid")
    content_hash = str(payload.get("content_hash") or "")
    generation_id = str(payload.get("generation_id") or "")
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != content_hash:
        raise Task055BOrchestrationError("task055b_final_report_content_hash_mismatch")
    if generation_id != f"task055b_result_{content_hash[:24]}" or report_path.parent.name != generation_id:
        raise Task055BOrchestrationError("task055b_final_report_generation_identity_mismatch")
    if payload.get("status") not in {BLOCKED_STATUS, COMPLETED_STATUS}:
        raise Task055BOrchestrationError("task055b_final_report_status_invalid")
    readiness = dict(payload.get("readiness") or {})
    if any(readiness.get(name) is not False for name in ("certification_ready", "portfolio_ready", "paper_ready", "live_ready")):
        raise Task055BOrchestrationError("task055b_final_report_downstream_readiness_invalid")
    queues = dict(payload.get("queues") or {})
    if set(queues) != set(PHYSICAL_STATE_NAMES) or any(int(value) != 0 for value in queues.values()):
        raise Task055BOrchestrationError("task055b_final_report_physical_queue_invalid")
    return payload | {"manifest_path": str(report_path)}


def _validate_lineage(
    inventory: Mapping[str, Any], request_plan: Mapping[str, Any], evidence: Mapping[str, Any],
    valuation: Mapping[str, Any], preflight: Mapping[str, Any],
) -> None:
    if int(request_plan.get("gap_cell_count", -1)) != int(inventory.get("cell_count", -2)):
        raise Task055BOrchestrationError("task055b_request_plan_inventory_count_mismatch")
    if int(evidence.get("record_count", -1)) != int(inventory.get("cell_count", -2)):
        raise Task055BOrchestrationError("task055b_evidence_inventory_count_mismatch")
    if valuation.get("evidence_content_hash") != evidence.get("content_hash"):
        raise Task055BOrchestrationError("task055b_valuation_evidence_lineage_mismatch")
    if preflight.get("evidence_content_hash") != evidence.get("content_hash"):
        raise Task055BOrchestrationError("task055b_preflight_evidence_lineage_mismatch")
    if preflight.get("valuation_content_hash") != valuation.get("content_hash"):
        raise Task055BOrchestrationError("task055b_preflight_valuation_lineage_mismatch")


def _blockers(
    inventory: Mapping[str, Any], request_plan: Mapping[str, Any], evidence: Mapping[str, Any],
    preflight: Mapping[str, Any], fee_schedule: Mapping[str, Any] | None, replay_complete: bool,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    unresolved = sum(
        int(value) for state, value in dict(evidence.get("state_counts") or {}).items()
        if state in {"TRADED_SOURCE_CONFLICT", "CALENDAR_OR_MEMBERSHIP_ERROR", "RAW_BAR_REQUIRED_FIELD_INVALID", "SOURCE_NORMALIZATION_ZERO_FILL", "CORPORATE_ACTION_VALUATION_UNPROVEN", "DATA_SOURCE_GAP", "CONFLICT"}
    )
    if unresolved:
        result.append({"code": "security_date_evidence_unresolved", "count": unresolved})
    metrics = dict(preflight.get("metrics") or {})
    if int(metrics.get("unresolved", 0)):
        result.append({"code": "valuation_reporting_points_unresolved", "count": int(metrics["unresolved"])})
    request_execution = dict(request_plan.get("execution") or {})
    remaining = int(request_execution.get("cache_miss_count", request_plan.get("request_count", 0)))
    if remaining and unresolved:
        result.append({"code": "governed_backfill_requests_remaining", "request_count": remaining})
    if fee_schedule is None:
        result.append({"code": "governed_fee_schedule_not_published"})
    if not replay_complete:
        result.append({"code": "simulation_replay_not_started_preflight_blocked"})
    return result


def _verify_physical_replay_tree(path: Any) -> dict[str, Any]:
    if path in (None, ""):
        return {"verified": False, "reason": "simulation_run_tree_missing"}
    root = Path(str(path))
    manifest_path = root / "task055b_replay_manifest.json"
    if not manifest_path.is_file():
        return {"verified": False, "reason": "simulation_replay_manifest_missing"}
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "task055b_native_replay_tree_v1":
        raise Task055BOrchestrationError("task055b_simulation_replay_schema_invalid")
    runs = list(payload.get("runs") or [])
    identities = {(str(row.get("factor_id")), str(row.get("scenario_id"))) for row in runs}
    if len(runs) != 100 or len(identities) != 100:
        raise Task055BOrchestrationError("task055b_simulation_replay_cartesian_set_invalid")
    for row in runs:
        artifact = root / str(row.get("manifest") or "")
        if not artifact.is_file() or sha256_file(artifact) != row.get("sha256"):
            raise Task055BOrchestrationError("task055b_simulation_replay_artifact_invalid")
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    if canonical_hash(semantic) != payload.get("content_hash"):
        raise Task055BOrchestrationError("task055b_simulation_replay_content_hash_invalid")
    return {"verified": True, "run_count": 100, "content_hash": payload.get("content_hash")}


def _summary(payload: Mapping[str, Any] | None, keys: Sequence[str]) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {key: payload.get(key) for key in keys}


def _publish(root: Path, report: Mapping[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    semantic = dict(report)
    content_hash = canonical_hash(semantic)
    generation_id = f"task055b_result_{content_hash[:24]}"
    payload = semantic | {"content_hash": content_hash, "generation_id": generation_id}
    staging = Path(tempfile.mkdtemp(prefix=".task055b_result.", dir=root))
    try:
        path = staging / "task055b_final_report.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = json.loads((target / path.name).read_text(encoding="utf-8"))
            if existing != payload:
                raise Task055BOrchestrationError("task055b_immutable_final_generation_conflict")
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        pointer = {"schema_version": FINAL_POINTER_SCHEMA, "generation_id": generation_id, "content_hash": content_hash, "manifest": f"generations/{generation_id}/{path.name}"}
        _atomic_json(root / "current.json", pointer)
        return payload | {"manifest_path": str(target / path.name)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fail-closed Task 055-B production evidence gate")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    print(json.dumps(run_task055b(config), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
