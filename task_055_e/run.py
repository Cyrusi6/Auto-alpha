"""Task 055-E offline-only source salvage orchestrator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from data_lake.task052_freeze import validate_task052_governed_freeze
from task_054_c.validators import validate_strict_matrix_generation
from task_055_a.bundle import validate_simulation_bundle
from task_055_a.observation import validate_observation_boundary_seal
from task_055_c.evidence import MODELED, validate_truth_table

from .contracts import (
    EXPECTED_BASELINE,
    FINAL_REPORT_SCHEMA,
    MAX_DATE,
    MAX_STALE_AGE_TRADE_DAYS,
    OFFLINE_BLOCKED_STATUS,
    OFFLINE_STAGE_STATUS,
    PROBE_KEYS,
)
from .domains import build_anchor_and_domain_generation, validate_anchor_and_domain_generation
from .provenance import (
    OfflineProvenanceError,
    canonical_hash,
    discover_raw_daily_source,
    scan_offline_sources,
    validate_offline_provenance,
)


class Task055EOfflineError(RuntimeError):
    pass


def run_offline_source_salvage(config: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    payload = _load(config)
    _validate_no_network(payload)
    git_state = _validate_git_baseline()
    governed_root = Path(str(payload["governed_data_root"])).resolve()
    output_root = Path(str(payload["output_root"])).resolve()
    if not governed_root.is_dir() or not _inside(governed_root, output_root):
        raise Task055EOfflineError("task055e_output_or_governed_root_invalid")
    parent = _resolve_parent(governed_root, payload)
    observation = validate_observation_boundary_seal(parent["observation_seal"], rescan=True)
    observed = observation.get("observation") or observation
    if str(observed.get("max_observed_target_endpoint") or "") > MAX_DATE:
        raise Task055EOfflineError("observation_boundary_exceeds_task055e_limit")
    truth = validate_truth_table(parent["truth_manifest"])
    freeze_root, freeze = _resolve_freeze(governed_root, parent["matrix_root"])
    raw_source = discover_raw_daily_source(governed_root)
    target_keys, target_summary = _derive_remediation_keys(truth, parent["matrix_root"])
    target_keys.update(PROBE_KEYS)

    provenance = scan_offline_sources(
        governed_root=governed_root,
        freeze_root=freeze_root,
        freeze_manifest=freeze,
        raw_source=raw_source,
        matrix_root=parent["matrix_root"],
        target_keys=target_keys,
        suspension_coverage_ledger=parent["suspension_coverage_ledger"],
        suspension_cache_root=parent["suspension_cache_root"],
        output_root=output_root / "offline_provenance",
        builder_code_hash=git_state["source_code_semantic_hash"],
    )
    validate_offline_provenance(provenance["manifest_path"], governed_root=governed_root)
    domains = build_anchor_and_domain_generation(
        truth_manifest=parent["truth_manifest"],
        matrix_root=parent["matrix_root"],
        simulation_bundle_manifest=parent["simulation_bundle"],
        provenance_manifest=provenance["manifest_path"],
        output_root=output_root / "offline_domains",
        builder_code_hash=git_state["source_code_semantic_hash"],
    )
    validate_anchor_and_domain_generation(domains["manifest_path"])
    report = _build_report(
        governed_root=governed_root,
        output_root=output_root,
        git_state=git_state,
        observation=observation,
        parent=parent,
        freeze=freeze,
        raw_source=raw_source,
        target_summary=target_summary,
        provenance=provenance,
        domains=domains,
    )
    result = _publish_report(output_root / "final", report)
    validate_offline_report(result["manifest_path"], governed_data_root=governed_root, output_root=output_root)
    return result


def validate_offline_report(
    path: str | Path,
    *,
    governed_data_root: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    report_path = Path(path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema_version") != FINAL_REPORT_SCHEMA or report.get("offline_stage_status") != OFFLINE_STAGE_STATUS:
        raise Task055EOfflineError("task055e_offline_report_schema_or_stage_invalid")
    if report.get("status") != OFFLINE_BLOCKED_STATUS:
        raise Task055EOfflineError("task055e_offline_report_must_not_claim_simulator_success")
    if report.get("network_accessed") is not False or report.get("prospective_holdout_accessed") is not False:
        raise Task055EOfflineError("task055e_offline_report_network_boundary_invalid")
    semantic = {key: value for key, value in report.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != report.get("content_hash"):
        raise Task055EOfflineError("task055e_offline_report_content_hash_mismatch")
    output = Path(output_root).resolve()
    provenance = output / report["artifacts"]["provenance_manifest"]
    domains = output / report["artifacts"]["domain_manifest"]
    validate_offline_provenance(provenance, governed_root=governed_data_root)
    validate_anchor_and_domain_generation(domains)
    return report | {"manifest_path": str(report_path)}


def _resolve_parent(governed_root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    task055c_root = _safe_relative(governed_root, payload["parent_task055c_run_relative"])
    report_path = task055c_root / "task055c_stdout_v2.json"
    config_path = task055c_root / "task055c_config.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    parent_config = json.loads(config_path.read_text(encoding="utf-8"))
    truth_manifest = _governed_path(governed_root, report["truth"]["manifest_path"])
    truth = validate_truth_table(truth_manifest)
    if truth["content_hash"] != report["truth"]["content_hash"]:
        raise Task055EOfflineError("task055e_parent_truth_hash_mismatch")
    matrix_root = _governed_path(governed_root, parent_config["matrix_root"])
    matrix_validation = validate_strict_matrix_generation(matrix_root)
    coverage_ledger = _governed_path(governed_root, parent_config["suspension_coverage_ledger"])
    suspension_cache_root = _governed_path(governed_root, parent_config["suspension_cache_root"])
    observation = _safe_relative(governed_root, payload["observation_seal_relative"])
    task055a_root = observation.parent.parent
    pointer_path = task055a_root / "simulation_bundles" / "current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    simulation_bundle = task055a_root / "simulation_bundles" / str(pointer["manifest"])
    bundle = validate_simulation_bundle(simulation_bundle, require_ready=True)
    if bundle["content_hash"] != pointer.get("content_hash"):
        raise Task055EOfflineError("task055e_simulation_bundle_pointer_mismatch")
    return {
        "truth_manifest": truth_manifest,
        "matrix_root": matrix_root,
        "suspension_coverage_ledger": coverage_ledger,
        "suspension_cache_root": suspension_cache_root,
        "observation_seal": observation,
        "simulation_bundle": simulation_bundle,
        "lineage": {
            "task055c_report_hash": canonical_hash(report),
            "truth_content_hash": truth["content_hash"],
            "simulation_bundle_hash": bundle["content_hash"],
            "matrix_content_hash": matrix_validation["content_hash"],
            "matrix_partition_count": matrix_validation["partition_count"],
        },
    }


def _resolve_freeze(governed_root: Path, matrix_root: Path) -> tuple[Path, dict[str, Any]]:
    matrix = json.loads((matrix_root / "task_052a_strict_matrix_manifest.json").read_text(encoding="utf-8"))
    freeze_hash = str((matrix.get("generation_inputs") or {}).get("governed_freeze_content_hash") or "")
    expected_daily_sha = str(((matrix.get("generation_inputs") or {}).get("artifact_sha256") or {}).get("daily_bars") or "")
    if not freeze_hash or not expected_daily_sha:
        raise Task055EOfflineError("task055e_matrix_freeze_lineage_missing")
    matches = []
    for candidate in sorted((governed_root / "validation_runs").glob("task_053*/**/freeze_manifest.json")):
        try:
            manifest = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("content_hash") == freeze_hash:
            matches.append((candidate.parent, manifest))
    if not matches:
        raise Task055EOfflineError("task055e_governed_freeze_not_found")
    freeze_root, freeze = matches[0]
    freeze_validation = validate_task052_governed_freeze(freeze_root, expected_content_hash=freeze_hash)
    daily = (freeze.get("datasets") or {}).get("daily_bars") or {}
    if daily.get("sha256") != expected_daily_sha:
        raise Task055EOfflineError("task055e_freeze_matrix_daily_sha_mismatch")
    freeze["authoritative_validation"] = {
        "checked_artifacts": freeze_validation["checked_artifacts"],
        "semantic_hash": freeze_validation["semantic_hash"],
    }
    return freeze_root, freeze


def _derive_remediation_keys(truth: Mapping[str, Any], matrix_root: Path) -> tuple[set[tuple[str, str]], dict[str, Any]]:
    codes = json.loads((matrix_root / "ts_codes.json").read_text(encoding="utf-8"))
    dates = json.loads((matrix_root / "trade_dates.json").read_text(encoding="utf-8"))
    code_index = {code: index for index, code in enumerate(codes)}
    date_index = {date: index for index, date in enumerate(dates)}
    close_valid = np.load(matrix_root / "close_validity.npy", mmap_mode="r", allow_pickle=False)
    last = np.full(len(codes), -1, dtype=np.int32)
    prior = np.full(close_valid.shape, -1, dtype=np.int32)
    for date_pos in range(len(dates)):
        prior[:, date_pos] = last
        valid = np.asarray(close_valid[:, date_pos], dtype=bool)
        last[valid] = date_pos
    unresolved: set[tuple[str, str]] = set()
    anchors: set[tuple[str, str]] = set()
    for row in truth["records"]:
        if not row.get("valuation_domain_intersection"):
            continue
        key = (str(row["ts_code"]), str(row["trade_date"]))
        if row.get("state") != MODELED:
            unresolved.add(key)
            continue
        if key[0] not in code_index or key[1] not in date_index:
            anchors.add(key)
            continue
        asset, day = code_index[key[0]], date_index[key[1]]
        source = int(prior[asset, day])
        if source < 0 or day - source > MAX_STALE_AGE_TRADE_DAYS or bool(close_valid[asset, day]):
            anchors.add(key)
    return unresolved | anchors, {
        "unresolved_evidence_keys": len(unresolved),
        "direct_reprojected_anchor_keys": len(anchors),
        "remediation_key_count_before_probes": len(unresolved | anchors),
        "stock_count_before_probes": len({code for code, _ in unresolved | anchors}),
    }


def _build_report(
    *,
    governed_root: Path,
    output_root: Path,
    git_state: Mapping[str, Any],
    observation: Mapping[str, Any],
    parent: Mapping[str, Any],
    freeze: Mapping[str, Any],
    raw_source: Mapping[str, Any],
    target_summary: Mapping[str, Any],
    provenance: Mapping[str, Any],
    domains: Mapping[str, Any],
) -> dict[str, Any]:
    domain_manifest = json.loads(Path(domains["manifest_path"]).read_text(encoding="utf-8"))
    domain_root = Path(domains["manifest_path"]).parent
    domain_payload = json.loads((domain_root / domain_manifest["partitions"]["domains"]["path"]).read_text(encoding="utf-8"))
    network_plan = json.loads((domain_root / domain_manifest["partitions"]["network_plan"]["path"]).read_text(encoding="utf-8"))
    causal = domain_payload["causal_held_position_valuation_domain"]
    observed = observation.get("observation") or observation
    blockers = []
    if provenance["offline_raw_repair_count"]:
        blockers.append({"code": "offline_raw_repair_requires_sibling_lineage_rebuild", "count": provenance["offline_raw_repair_count"]})
    if causal["remaining_security_date_count"]:
        blockers.append({"code": "causal_held_position_valuation_evidence_remaining", "count": causal["remaining_security_date_count"]})
    infrastructure = int(causal["terminal_counts"].get("causal_infrastructure_blocked", 0))
    if infrastructure:
        blockers.append({"code": "causal_simulation_infrastructure_blocked", "run_count": infrastructure})
    if network_plan["estimated_daily_request_count"] or network_plan["estimated_suspend_d_request_count"]:
        blockers.append(
            {
                "code": "offline_stock_exhausted_minimal_network_plan_sealed",
                "daily_requests": network_plan["estimated_daily_request_count"],
                "suspend_d_requests": network_plan["estimated_suspend_d_request_count"],
            }
        )
    blockers.extend(
        {"code": code}
        for code in (
            "future_research_data_incomplete",
            "historical_selection_contamination",
            "execution_modeled",
            "suspension_timing_semantics_uncertified",
            "constituent_publication_timing_unknown",
            "vendor_historical_revision_risk",
            "prospective_holdout_not_arrived",
        )
    )
    return {
        "schema_version": FINAL_REPORT_SCHEMA,
        "status": OFFLINE_BLOCKED_STATUS,
        "offline_stage_status": OFFLINE_STAGE_STATUS,
        "network_accessed": False,
        "network_request_count": 0,
        "credential_required": False,
        "credential_present_checked": False,
        "prospective_holdout_accessed": False,
        "max_read_or_request_date": MAX_DATE,
        "git": dict(git_state),
        "observation_boundary": {
            "content_hash": observation.get("content_hash"),
            "max_project_observed_signal_date": observed.get("max_observed_signal_date"),
            "max_project_observed_source_date": observed.get("max_observed_source_date"),
            "max_project_observed_target_endpoint": observed.get("max_observed_target_endpoint"),
        },
        "lineage": {
            **parent["lineage"],
            "governed_freeze_content_hash": freeze["content_hash"],
            "governed_freeze_semantic_hash": (freeze.get("authoritative_validation") or {}).get("semantic_hash"),
            "governed_freeze_checked_artifacts": (freeze.get("authoritative_validation") or {}).get("checked_artifacts"),
            "raw_index_declaration": _relative(governed_root, Path(raw_source["index_path"])),
            "raw_daily_sha256": raw_source["declared_sha256"],
            "provenance_content_hash": provenance["content_hash"],
            "domain_content_hash": domains["content_hash"],
        },
        "target_summary": dict(target_summary),
        "classification_counts": provenance["classification_counts"],
        "offline_raw_repair_count": provenance["offline_raw_repair_count"],
        "anchor_count": domain_manifest["anchor_count"],
        "anchor_cause_counts": domain_manifest["anchor_cause_counts"],
        "valuation_domains": domain_payload,
        "minimal_network_plan": {
            key: network_plan[key]
            for key in (
                "existing_data_directly_resolved",
                "offline_raw_repair_resolved",
                "simulator_held_domain_remaining_security_dates",
                "remaining_stock_count",
                "remaining_date_count",
                "remaining_episode_count",
                "estimated_daily_request_count",
                "estimated_suspend_d_request_count",
                "fixed_probes",
                "plan_hash",
            )
        },
        "artifacts": {
            "provenance_manifest": _relative(output_root, Path(provenance["manifest_path"])),
            "domain_manifest": _relative(output_root, Path(domains["manifest_path"])),
        },
        "readiness": {
            "offline_source_salvage_ready": True,
            "factor_replay_ready": True,
            "continuous_portfolio_valuation_ready": bool(causal["closed"]),
            "simulator_engineering_ready": False,
            "future_research_data_ready": False,
            "certification_ready": False,
            "portfolio_ready": False,
            "optimizer_ready": False,
            "paper_ready": False,
            "live_ready": False,
        },
        "simulator_success_evidence_created": False,
        "blockers": blockers,
    }


def _publish_report(root: Path, report: Mapping[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    semantic = dict(report)
    content_hash = canonical_hash(semantic)
    generation_id = f"offline_report_{content_hash[:24]}"
    payload = semantic | {"content_hash": content_hash, "generation_id": generation_id}
    staging = Path(tempfile.mkdtemp(prefix=".task055e.report.", dir=root))
    try:
        path = staging / "task055e_offline_report.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        pointer = root / "current.json"
        temporary = pointer.with_name(f".{pointer.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps({"generation_id": generation_id, "content_hash": content_hash, "manifest": f"generations/{generation_id}/task055e_offline_report.json"}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, pointer)
        return payload | {"manifest_path": str(target / "task055e_offline_report.json")}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _validate_no_network(payload: Mapping[str, Any]) -> None:
    forbidden = {
        "allow_network",
        "request_plan_hash",
        "request_budget",
        "credential_file",
        "tushare_token",
        "simulation_run_root",
        "simulation_replay_evidence",
    }
    present = sorted(key for key in forbidden if key in payload and payload.get(key) not in {None, False, 0, ""})
    if present:
        raise Task055EOfflineError(f"task055e_offline_forbidden_network_or_injected_input:{','.join(present)}")


def _validate_git_baseline() -> dict[str, Any]:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    remote = subprocess.check_output(["git", "rev-parse", "origin/main"], text=True).strip()
    head_ok = subprocess.run(["git", "merge-base", "--is-ancestor", EXPECTED_BASELINE, head], check=False).returncode == 0
    remote_ok = subprocess.run(["git", "merge-base", "--is-ancestor", EXPECTED_BASELINE, remote], check=False).returncode == 0
    if not head_ok or not remote_ok:
        raise Task055EOfflineError(f"task055e_baseline_mismatch:{head}:{remote}")
    return {
        "baseline": EXPECTED_BASELINE,
        "head": head,
        "origin_main": remote,
        "baseline_is_head_ancestor": head_ok,
        "baseline_is_origin_main_ancestor": remote_ok,
        "source_code_semantic_hash": _code_semantic_hash(),
    }


def _code_semantic_hash() -> str:
    repository = Path(__file__).resolve().parents[1]
    paths = sorted((repository / "task_055_e").glob("*.py")) + [
        repository / "task_055_a" / "run.py",
        repository / "task_055_a" / "simulator.py",
        repository / "task_055_a" / "policy.py",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(repository)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _safe_relative(root: Path, value: Any) -> Path:
    relative = Path(str(value))
    if relative.is_absolute() or ".." in relative.parts:
        raise Task055EOfflineError("task055e_relative_input_invalid")
    path = (root / relative).resolve()
    if not _inside(root, path):
        raise Task055EOfflineError("task055e_relative_input_escaped")
    return path


def _governed_path(root: Path, value: Any) -> Path:
    path = Path(str(value)).resolve()
    if not _inside(root, path):
        raise Task055EOfflineError("task055e_parent_path_outside_governed_root")
    return path


def _relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if not _inside(root.resolve(), resolved):
        raise Task055EOfflineError("task055e_artifact_path_outside_root")
    return str(resolved.relative_to(root.resolve()))


def _inside(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


def _load(config: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(config, Mapping):
        return dict(config)
    return json.loads(Path(config).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Task 055-E offline source salvage without credentials or network")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    result = run_offline_source_salvage(args.config)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
