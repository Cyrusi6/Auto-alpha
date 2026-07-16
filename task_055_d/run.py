"""Task 055-D production orchestrator.

The runner derives all Task 055-C inputs from one governed root, publishes the
sealed L0/L1 plan, performs cache/TLS/credential gating, builds an immutable
full-axis valuation generation, and refuses simulator creation until every
closure gate is independently verified.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

from data_pipeline.ashare.request_normalization import stable_json_hash
from task_055_a.observation import validate_observation_boundary_seal
from task_055_c.evidence import validate_truth_table

from .contracts import BLOCKED
from .fees import FeeScheduleV2Error, validate_fee_schedule_v2
from .network import execute_plan
from .operational import OperationalStateError, inspect_canonical_operational_root
from .planner import build_l0_l1_plan
from .valuation import build_full_axis_valuation, validate_full_axis_valuation

EXPECTED_BASELINE = "1afba9367bcef5454b2a6a2279421d9a5622ae2a"


class Task055DOrchestrationError(RuntimeError):
    pass


def run_task055d(config: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    payload = _load(config)
    _validate_forbidden_keys(payload)
    _validate_git_baseline()
    governed_root = Path(payload["governed_data_root"]).resolve()
    output_root = Path(payload["output_root"]).resolve()
    if governed_root != output_root and governed_root not in output_root.parents:
        raise Task055DOrchestrationError("task055d_output_outside_governed_root")
    parent = _resolve_parent(governed_root, payload)
    observation = validate_observation_boundary_seal(parent["observation_seal"], rescan=True)
    plan = build_l0_l1_plan(
        parent_truth_manifest=parent["truth_manifest"],
        parent_valuation_manifest=parent["valuation_manifest"],
        matrix_root=parent["matrix_root"],
        output_root=output_root / "plans",
    )
    network = execute_plan(
        plan=plan,
        output_root=output_root / "network",
        cache_roots=parent["cache_roots"],
        allow_network=bool(payload.get("allow_network", False)),
        sealed_plan_hash=payload.get("request_plan_hash"),
        request_budget=int(payload.get("request_budget", 0)),
        trade_dates=json.loads((Path(parent["matrix_root"]) / "trade_dates.json").read_text(encoding="utf-8")),
    )
    valuation = build_full_axis_valuation(
        truth_manifest=parent["truth_manifest"],
        matrix_root=parent["matrix_root"],
        output_root=output_root / "valuation",
    )
    validate_full_axis_valuation(valuation["manifest_path"], truth_manifest=parent["truth_manifest"], matrix_root=parent["matrix_root"])
    fee = _validate_fee(payload.get("fee_schedule_v2"))
    operational = _inspect_operational(governed_root)
    blockers = _blockers(network, valuation, fee, operational)
    if not blockers:
        raise Task055DOrchestrationError("simulator_gate_reached_but_native_task055d_replay_not_configured")
    report = {
        "schema_version": "task055d_final_report_v1",
        "status": BLOCKED,
        "parent_lineage": parent["lineage"],
        "observation_seal_hash": observation["content_hash"],
        "plan": _summary(plan),
        "network": _summary(network),
        "valuation": _summary(valuation),
        "fee_schedule_v2": fee,
        "operational_state": operational,
        "readiness": {
            "factor_replay_ready": True,
            "continuous_portfolio_valuation_ready": False,
            "simulator_engineering_ready": False,
            "future_research_data_ready": False,
            "certification_ready": False,
            "portfolio_ready": False,
            "optimizer_ready": False,
            "paper_ready": False,
            "live_ready": False,
        },
        "prospective_holdout_accessed": bool(network["prospective_holdout_accessed"]),
        "historical_selection_contaminated": True,
        "execution_evidence_level": "modeled_daily_bar_proxy",
        "blockers": blockers + [
            {"code": "historical_selection_contamination"},
            {"code": "execution_modeled"},
            {"code": "suspension_timing_semantics_uncertified"},
            {"code": "constituent_publication_timing_unknown"},
            {"code": "vendor_historical_revision_risk"},
            {"code": "prospective_holdout_not_arrived"},
        ],
    }
    return _publish(output_root / "final", report)


def _resolve_parent(governed_root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    run_relative = Path(str(payload["parent_task055c_run_relative"]))
    if run_relative.is_absolute() or ".." in run_relative.parts:
        raise Task055DOrchestrationError("task055d_parent_relative_path_invalid")
    run_root = (governed_root / run_relative).resolve()
    if governed_root not in run_root.parents:
        raise Task055DOrchestrationError("task055d_parent_outside_governed_root")
    report_path = run_root / "task055c_stdout_v2.json"
    config_path = run_root / "task055c_config.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    parent_config = json.loads(config_path.read_text(encoding="utf-8"))
    truth = validate_truth_table(report["truth"]["manifest_path"])
    valuation_path = Path(report["valuation"]["manifest_path"])
    valuation = json.loads(valuation_path.read_text(encoding="utf-8"))
    if truth["content_hash"] != report["truth"]["content_hash"] or valuation["content_hash"] != report["valuation"]["content_hash"]:
        raise Task055DOrchestrationError("task055d_parent_hash_mismatch")
    matrix_root = Path(parent_config["matrix_root"]).resolve()
    for path in (Path(report["truth"]["manifest_path"]), valuation_path, matrix_root):
        if governed_root != path.resolve() and governed_root not in path.resolve().parents:
            raise Task055DOrchestrationError("task055d_parent_artifact_outside_governed_root")
    observation_relative = Path(str(payload["observation_seal_relative"]))
    observation = (governed_root / observation_relative).resolve()
    if observation_relative.is_absolute() or governed_root not in observation.parents:
        raise Task055DOrchestrationError("task055d_observation_seal_path_invalid")
    return {
        "truth_manifest": str(Path(report["truth"]["manifest_path"])),
        "valuation_manifest": str(valuation_path),
        "matrix_root": str(matrix_root),
        "cache_roots": [str(Path(value)) for value in parent_config.get("cache_roots") or ()],
        "observation_seal": str(observation),
        "lineage": {
            "task055c_report_hash": stable_json_hash(report),
            "task055c_truth_hash": truth["content_hash"],
            "task055c_valuation_hash": valuation["content_hash"],
            "matrix_content_hash": json.loads((matrix_root / "task_052a_strict_matrix_manifest.json").read_text(encoding="utf-8"))["content_hash"],
        },
    }


def _validate_git_baseline() -> None:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", EXPECTED_BASELINE, head], check=False)
    if ancestor.returncode != 0:
        raise Task055DOrchestrationError(f"task055d_git_baseline_not_ancestor:{head}")


def _validate_forbidden_keys(payload: Mapping[str, Any]) -> None:
    forbidden = {"inventory_manifest", "suspension_records", "matrix_root", "simulation_run_root", "success_manifest", "simulation_replay_evidence"}
    present = sorted(forbidden & set(payload))
    if present:
        raise Task055DOrchestrationError(f"task055d_injected_inputs_forbidden:{','.join(present)}")


def _validate_fee(path: Any) -> dict[str, Any]:
    if not path:
        return {"status": "blocked", "blocker": "fee_schedule_v2_missing"}
    try:
        fee = validate_fee_schedule_v2(path)
        return {"status": "passed", "content_hash": fee["content_hash"]}
    except (FeeScheduleV2Error, OSError, ValueError) as exc:
        return {"status": "blocked", "blocker": str(exc)}


def _inspect_operational(root: Path) -> dict[str, Any]:
    try:
        return inspect_canonical_operational_root(root)
    except OperationalStateError as exc:
        return {"status": "blocked", "blocker": str(exc), "states": {}}


def _blockers(network: Mapping[str, Any], valuation: Mapping[str, Any], fee: Mapping[str, Any], operational: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    if network.get("status") != "complete":
        result.append({"code": str(network.get("blocker") or "governed_network_incomplete"), "remaining": int(network.get("remaining_count", 0))})
    if int(valuation.get("unresolved_reporting_points", 0)):
        result.append({"code": "valuation_reporting_points_unresolved", "count": int(valuation["unresolved_reporting_points"])})
    if int(valuation.get("lineage_conflict_count", 0)):
        result.append({"code": "valuation_lineage_conflicts", "count": int(valuation["lineage_conflict_count"])})
    if fee.get("status") != "passed":
        result.append({"code": str(fee.get("blocker") or "fee_schedule_v2_blocked")})
    if operational.get("status") != "passed":
        result.append({"code": "canonical_operational_state_not_proven_empty"})
    return result


def _summary(value: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("status", "content_hash", "manifest_path", "unresolved_evidence_cells", "modeled_unmarked_cells", "anchor_cause_counts", "l1_stock_count", "physical_attempt_count", "network_spend", "validated_cache_hit_count", "remaining_count", "reporting_points", "covered_reporting_points", "unresolved_reporting_points", "lineage_conflict_count", "illegal_carry_count", "stock_axis_hash", "date_axis_hash")
    return {key: value[key] for key in keys if key in value}


def _publish(root: Path, report: dict[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    content_hash = stable_json_hash(report)
    payload = report | {"content_hash": content_hash}
    path = root / f"task055d_report_{content_hash[:24]}.json"
    temporary = root / f".{path.name}.tmp"
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return payload | {"manifest_path": str(path)}


def _load(config: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(config, Mapping):
        return dict(config)
    return json.loads(Path(config).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Task 055-D secure remediation orchestration")
    parser.add_argument("--config", required=True)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--request-plan-hash")
    args = parser.parse_args()
    config = _load(args.config)
    if args.allow_network:
        config["allow_network"] = True
    if args.request_plan_hash:
        config["request_plan_hash"] = args.request_plan_hash
    result = run_task055d(config)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == BLOCKED else 1


if __name__ == "__main__":
    raise SystemExit(main())
