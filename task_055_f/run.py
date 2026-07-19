"""Unique Task 055-F offline-hardening production entrypoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from task_055_a.bundle import validate_simulation_bundle
from task_055_a.observation import validate_observation_boundary_seal
from task_055_d.operational import OperationalStateError, inspect_canonical_operational_root

from .causal import build_causal_frontier, validate_causal_frontier
from .contracts import (
    BLOCKED_STATUS,
    COMPLETED_STATUS,
    EXPECTED_BASELINE,
    EXPECTED_TASK055E_REPORT_HASH,
    FINAL_REPORT_SCHEMA,
    MAX_DATE,
)
from .fees import FeeScheduleError, validate_fee_schedule_v2
from .network import (
    credential_presence,
    execute_canary,
    execute_l1_resume,
    verify_canary,
)
from .read_ledger import AuditedReader, canonical_hash, validate_read_ledger
from .replay import run_native_replay, verify_native_replay_tree
from .truth_v2 import build_truth_v2, validate_truth_v2
from .verifier import validate_semantic_verification, verify_task055f_semantics


class Task055FError(RuntimeError):
    pass


def run_offline_hardening(config: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    payload = _load(config)
    _reject_network_or_injected_inputs(payload)
    git = _validate_git_baseline()
    governed = Path(str(payload["governed_data_root"])).resolve()
    output = Path(str(payload["output_root"])).resolve()
    if not governed.is_dir() or governed not in output.parents:
        raise Task055FError("task055f_governed_or_output_root_invalid")
    if "task_055_e" in output.parts:
        raise Task055FError("task055f_output_must_not_overlap_task055e")
    parents = _resolve_parents(governed)
    reader = AuditedReader(governed)
    reader.read_json(
        parents["observation_seal"],
        component="task055f_orchestrator",
        dataset="observation_boundary_seal",
    )
    observation = validate_observation_boundary_seal(parents["observation_seal"], rescan=True)
    observed = observation.get("observation") or observation
    if str(observed.get("max_observed_target_endpoint") or "") > MAX_DATE:
        raise Task055FError("task055f_observation_boundary_exceeds_max_date")

    truth = build_truth_v2(
        governed_root=governed,
        inventory_manifest=parents["inventory_manifest"],
        matrix_root=parents["matrix_root"],
        suspension_coverage_ledger=parents["suspension_coverage_ledger"],
        suspension_cache_root=parents["suspension_cache_root"],
        task055e_provenance_manifest=parents["task055e_provenance_manifest"],
        task055c_truth_manifest=parents["task055c_truth_manifest"],
        output_root=output / "truth_v2",
        reader=reader,
        builder_code_hash=git["source_code_semantic_hash"],
    )
    truth = validate_truth_v2(truth["manifest_path"])

    fee = _discover_fee_schedule(governed, payload.get("fee_schedule_content_hash"))
    causal = None
    if fee is not None:
        causal = build_causal_frontier(
            truth_v2_manifest=truth["manifest_path"],
            matrix_root=parents["matrix_root"],
            simulation_bundle_manifest=parents["simulation_bundle"],
            fee_schedule_manifest=fee["manifest_path"],
            output_root=output / "causal_frontier",
            reader=reader,
            builder_code_hash=git["source_code_semantic_hash"],
        )
        validate_causal_frontier(causal["manifest_path"])

    operational = _inspect_operational_state(governed)
    replay = None
    if (
        fee is not None
        and causal is not None
        and int(causal.get("round_one_frontier_count") or 0) == 0
        and operational.get("status") == "passed"
    ):
        replay = run_native_replay(
            causal_manifest=causal["manifest_path"],
            simulation_bundle_manifest=parents["simulation_bundle"],
            fee_schedule_manifest=fee["manifest_path"],
            output_root=output,
        )

    read_ledger = reader.publish(output / "read_ledger")
    validate_read_ledger(read_ledger["manifest_path"], governed_root=governed)
    verification = verify_task055f_semantics(
        truth_v2_manifest=truth["manifest_path"],
        governed_root=governed,
        matrix_root=parents["matrix_root"],
        read_ledger_manifest=read_ledger["manifest_path"],
        output_root=output / "semantic_verification",
        causal_manifest=causal["manifest_path"] if causal else None,
    )
    validate_semantic_verification(verification["manifest_path"], governed_root=governed)
    credential = credential_presence()
    report = _build_report(
        git=git,
        governed=governed,
        output=output,
        parents=parents,
        observation=observation,
        truth=truth,
        fee=fee,
        causal=causal,
        read_ledger=read_ledger,
        verification=verification,
        credential=credential,
        operational=operational,
        replay=replay,
    )
    result = _publish_report(output / "final", report)
    validate_offline_report(result["manifest_path"], governed_root=governed, output_root=output)
    return result


def validate_offline_report(
    path: str | Path,
    *,
    governed_root: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != FINAL_REPORT_SCHEMA or manifest.get("status") not in {
        BLOCKED_STATUS,
        COMPLETED_STATUS,
    }:
        raise Task055FError("task055f_report_schema_or_status_invalid")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise Task055FError("task055f_report_content_hash_mismatch")
    output = Path(output_root).resolve()
    truth = output / manifest["artifacts"]["truth_v2_manifest"]
    ledger = output / manifest["artifacts"]["read_ledger_manifest"]
    verification = output / manifest["artifacts"]["semantic_verification_manifest"]
    validate_truth_v2(truth)
    validate_read_ledger(ledger, governed_root=governed_root)
    validate_semantic_verification(verification, governed_root=governed_root)
    causal_relative = manifest["artifacts"].get("causal_manifest")
    if causal_relative:
        validate_causal_frontier(output / causal_relative)
    if manifest.get("status") == COMPLETED_STATUS:
        verified = verify_native_replay_tree(output)
        if verified.get("verification_hash") != manifest.get("native_replay", {}).get("verification_hash"):
            raise Task055FError("task055f_native_replay_verification_mismatch")
    if manifest.get("prospective_holdout_accessed") is not False:
        raise Task055FError("task055f_report_future_access_invalid")
    return manifest | {"manifest_path": str(manifest_path)}


def _resolve_parents(governed: Path) -> dict[str, Any]:
    selected = None
    for pointer in sorted((governed / "validation_runs").glob("task_055_e*/final/current.json")):
        try:
            current = json.loads(pointer.read_text(encoding="utf-8"))
            report_path = pointer.parent / str(current["manifest"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            continue
        if report.get("content_hash") == EXPECTED_TASK055E_REPORT_HASH:
            selected = (pointer.parent.parent, report_path, report)
            break
    if selected is None:
        raise Task055FError("expected_task055e_parent_not_found")
    task055e_root, report_path, report = selected
    config_path = task055e_root / "task055e_offline_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    task055c_root = _safe_relative(governed, config["parent_task055c_run_relative"])
    task055c_report = json.loads((task055c_root / "task055c_stdout_v2.json").read_text(encoding="utf-8"))
    task055c_config = json.loads((task055c_root / "task055c_config.json").read_text(encoding="utf-8"))
    if canonical_hash(task055c_report) != report["lineage"]["task055c_report_hash"]:
        raise Task055FError("task055c_parent_hash_mismatch")
    provenance = task055e_root / report["artifacts"]["provenance_manifest"]
    observation = _safe_relative(governed, config["observation_seal_relative"])
    task055a_root = observation.parent.parent
    bundle_pointer = json.loads((task055a_root / "simulation_bundles" / "current.json").read_text(encoding="utf-8"))
    simulation_bundle = task055a_root / "simulation_bundles" / str(bundle_pointer["manifest"])
    bundle = validate_simulation_bundle(simulation_bundle, require_ready=True)
    if bundle.get("content_hash") != report["lineage"]["simulation_bundle_hash"]:
        raise Task055FError("task055a_bundle_parent_hash_mismatch")
    resolved = {
        "task055e_root": task055e_root,
        "task055e_report": report_path,
        "task055e_provenance_manifest": provenance,
        "task055c_root": task055c_root,
        "task055c_truth_manifest": _governed_path(governed, task055c_report["truth"]["manifest_path"]),
        "inventory_manifest": _governed_path(governed, task055c_config["inventory_manifest"]),
        "matrix_root": _governed_path(governed, task055c_config["matrix_root"]),
        "suspension_coverage_ledger": _governed_path(
            governed, task055c_config["suspension_coverage_ledger"]
        ),
        "suspension_cache_root": _governed_path(governed, task055c_config["suspension_cache_root"]),
        "observation_seal": observation,
        "simulation_bundle": simulation_bundle,
    }
    return resolved | {
        "relative": {
            key: str(value.relative_to(governed))
            for key, value in resolved.items()
            if isinstance(value, Path)
        }
    }


def _discover_fee_schedule(governed: Path, expected_hash: Any) -> dict[str, Any] | None:
    candidates = []
    for pointer in sorted((governed / "validation_runs").glob("task_055_f*/fee_schedule/current.json")):
        try:
            current = json.loads(pointer.read_text(encoding="utf-8"))
            manifest_path = pointer.parent / str(current["manifest"])
            manifest = validate_fee_schedule_v2(manifest_path)
        except (OSError, ValueError, KeyError, FeeScheduleError):
            continue
        if expected_hash and manifest.get("content_hash") != str(expected_hash):
            continue
        candidates.append(manifest)
    if expected_hash and not candidates:
        raise Task055FError("expected_fee_schedule_not_found")
    if len(candidates) > 1 and not expected_hash:
        raise Task055FError("multiple_fee_schedules_require_content_hash")
    return candidates[0] if candidates else None


def _build_report(
    *,
    git: Mapping[str, Any],
    governed: Path,
    output: Path,
    parents: Mapping[str, Any],
    observation: Mapping[str, Any],
    truth: Mapping[str, Any],
    fee: Mapping[str, Any] | None,
    causal: Mapping[str, Any] | None,
    read_ledger: Mapping[str, Any],
    verification: Mapping[str, Any],
    credential: Mapping[str, Any],
    operational: Mapping[str, Any],
    replay: Mapping[str, Any] | None,
) -> dict[str, Any]:
    engineering_blockers = []
    if fee is None:
        engineering_blockers.append({"code": "official_fee_schedule_v2_unavailable"})
    if causal is None:
        engineering_blockers.append({"code": "round_one_frontier_not_sealed_without_fee_v2"})
    elif causal.get("round_one_frontier_count"):
        engineering_blockers.append(
            {
                "code": "round_one_causal_frontier_remaining",
                "count": causal["round_one_frontier_count"],
                "semantics": "first_terminal_blocker_frontier_not_total_gap_count",
            }
        )
    if operational.get("status") != "passed":
        missing_states = sorted(
            name
            for name, state in (operational.get("states") or {}).items()
            if state.get("status") != "empty"
        )
        engineering_blockers.append(
            {
                "code": "canonical_operational_state_not_proven_empty",
                "detail": operational.get("blocker") or f"nonempty_or_missing={missing_states}",
            }
        )
    if causal is not None and not causal.get("round_one_frontier_count") and replay is None:
        engineering_blockers.append({"code": "native_replay_not_completed_after_closed_frontier"})
    if not credential.get("credential_present"):
        engineering_blockers.append(
            {
                "code": "credential_unavailable",
                "activation_gate": "only_after_fee_v2_and_round_one_frontier_are_sealed",
            }
        )
    certification_blockers = [
        {"code": code}
        for code in (
            "historical_selection_contamination",
            "selection_data_reused",
            "execution_modeled",
            "suspension_timing_semantics_uncertified",
            "constituent_publication_timing_unknown",
            "vendor_historical_revision_risk",
            "prospective_holdout_not_arrived",
        )
    ]
    observed = observation.get("observation") or observation
    status = COMPLETED_STATUS if replay is not None and not engineering_blockers else BLOCKED_STATUS
    return {
        "schema_version": FINAL_REPORT_SCHEMA,
        "status": status,
        "stage": "native_replay_verified" if replay is not None else "offline_truth_hardening_completed",
        "network_accessed": False,
        "network_request_count": 0,
        "prospective_holdout_accessed": bool(read_ledger.get("prospective_holdout_accessed")),
        "max_read_date": read_ledger.get("max_read_date"),
        "git": dict(git),
        "observation_boundary": {
            "content_hash": observation.get("content_hash"),
            "max_observed_signal_date": observed.get("max_observed_signal_date"),
            "max_observed_source_date": observed.get("max_observed_source_date"),
            "max_observed_target_endpoint": observed.get("max_observed_target_endpoint"),
        },
        "parent_lineage": {
            "task055e_report_content_hash": EXPECTED_TASK055E_REPORT_HASH,
            "task055c_truth_lineage_only": truth["lineage"]["task055c_truth_lineage_only"],
            "matrix_content_hash": truth["lineage"]["matrix_content_hash"],
            "simulation_bundle_relative": parents["relative"]["simulation_bundle"],
        },
        "truth_v2": {
            "content_hash": truth["content_hash"],
            "record_count": truth["record_count"],
            "state_counts": truth["state_counts"],
            "suspend_type_counts": truth["suspend_type_counts"],
            "daily_empty_response_counts": truth["daily_empty_response_counts"],
            "suspend_empty_response_counts": truth["suspend_empty_response_counts"],
            "modeled_candidate_count": truth["modeled_candidate_count"],
            "stale_marks_authorized_by_truth": 0,
            "regression_probes": [
                {
                    "ts_code": row["ts_code"],
                    "trade_date": row["trade_date"],
                    "state": row["state"],
                    "reason_code": row["reason_code"],
                    "suspend_type": row["suspend_type"],
                    "suspend_timing_status": row["suspend_timing_status"],
                }
                for row in truth.get("records") or ()
                if row.get("regression_probe")
            ],
        },
        "fee_schedule_v2": None
        if fee is None
        else {"content_hash": fee["content_hash"], "status": fee["status"]},
        "causal_frontier": None
        if causal is None
        else {
            "content_hash": causal["content_hash"],
            "run_count": causal["run_count"],
            "terminal_counts": causal["terminal_counts"],
            "round_one_frontier_count": causal["round_one_frontier_count"],
            "round_one_frontier_semantics": causal["round_one_frontier_semantics"],
            "held_mark_count": causal["held_mark_count"],
            "authorized_modeled_held_mark_count": causal["authorized_modeled_held_mark_count"],
            "missing_key_root": causal["missing_key_root"],
        },
        "credential": dict(credential),
        "operational_state": dict(operational),
        "native_replay": None if replay is None else dict(replay),
        "ready_for_canary": bool(fee and causal and causal.get("round_one_frontier_count") and credential.get("credential_present")),
        "artifacts": {
            "truth_v2_manifest": _relative(output, Path(truth["manifest_path"])),
            "causal_manifest": _relative(output, Path(causal["manifest_path"])) if causal else None,
            "read_ledger_manifest": _relative(output, Path(read_ledger["manifest_path"])),
            "semantic_verification_manifest": _relative(output, Path(verification["manifest_path"])),
        },
        "readiness": {
            "truth_v2_ready": True,
            "fee_schedule_v2_ready": fee is not None,
            "causal_frontier_ready": causal is not None,
            "simulator_engineering_ready": replay is not None,
            "future_research_data_ready": False,
            "certification_ready": False,
            "portfolio_ready": False,
            "paper_ready": False,
            "live_ready": False,
        },
        "engineering_blockers": engineering_blockers,
        "certification_blockers": certification_blockers,
        "blockers": engineering_blockers + certification_blockers,
    }


def _inspect_operational_state(governed: Path) -> dict[str, Any]:
    try:
        return inspect_canonical_operational_root(governed)
    except (OperationalStateError, OSError, ValueError) as exc:
        return {
            "status": "blocked",
            "root_relative": "operational_state",
            "states": {},
            "blocker": str(exc),
        }


def _publish_report(root: Path, report: Mapping[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    content_hash = canonical_hash(report)
    generation_id = f"task055f_report_{content_hash[:24]}"
    payload = dict(report) | {"content_hash": content_hash, "generation_id": generation_id}
    staging = Path(tempfile.mkdtemp(prefix=".task055f.report.", dir=root))
    try:
        (staging / "task055f_report.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_json(
            root / "current.json",
            {
                "generation_id": generation_id,
                "content_hash": content_hash,
                "manifest": f"generations/{generation_id}/task055f_report.json",
            },
        )
        return payload | {"manifest_path": str(target / "task055f_report.json")}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _validate_git_baseline() -> dict[str, Any]:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    remote = subprocess.check_output(["git", "rev-parse", "origin/main"], text=True).strip()
    head_ok = subprocess.run(["git", "merge-base", "--is-ancestor", EXPECTED_BASELINE, head], check=False).returncode == 0
    remote_ok = subprocess.run(["git", "merge-base", "--is-ancestor", EXPECTED_BASELINE, remote], check=False).returncode == 0
    if not head_ok or not remote_ok:
        raise Task055FError(f"task055f_baseline_mismatch:{head}:{remote}")
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
    paths = sorted((repository / "task_055_f").glob("*.py")) + [
        repository / "task_055_a" / "models.py",
        repository / "task_055_a" / "simulator.py",
        repository / "task_055_a" / "verifier.py",
        repository / "data_pipeline" / "ashare" / "cache.py",
        repository / "data_pipeline" / "ashare" / "providers" / "tushare_client.py",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(repository)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _reject_network_or_injected_inputs(payload: Mapping[str, Any]) -> None:
    forbidden = {
        "allow_network",
        "sealed_plan_hash",
        "credential_file",
        "tushare_token",
        "simulation_run_root",
        "success_manifest",
        "truth_v2_manifest",
        "matrix_root",
        "inventory_manifest",
        "factor_store",
    }
    present = sorted(key for key in forbidden if payload.get(key) not in {None, False, 0, ""})
    if present:
        raise Task055FError(f"task055f_offline_injected_or_network_input_forbidden:{','.join(present)}")


def _load(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return json.loads(Path(value).read_text(encoding="utf-8"))


def _safe_relative(root: Path, value: Any) -> Path:
    relative = Path(str(value))
    if relative.is_absolute() or ".." in relative.parts:
        raise Task055FError("task055f_parent_relative_path_invalid")
    path = (root / relative).resolve()
    if root not in path.parents:
        raise Task055FError("task055f_parent_relative_path_escape")
    return path


def _governed_path(root: Path, value: Any) -> Path:
    path = Path(str(value)).resolve()
    if root not in path.parents:
        raise Task055FError("task055f_parent_absolute_path_outside_governed_root")
    return path


def _relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if root not in resolved.parents:
        raise Task055FError("task055f_artifact_outside_output_root")
    return str(resolved.relative_to(root))


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 055-F hardened evidence and staged acquisition")
    parser.add_argument("command", choices=("offline", "canary", "canary-verify", "l1-resume"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--sealed-plan-hash")
    args = parser.parse_args()
    config = _load(args.config)
    if args.command == "offline":
        if args.allow_network or args.sealed_plan_hash:
            raise Task055FError("offline_command_forbids_network_authorization")
        result = run_offline_hardening(config)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0 if result.get("status") == COMPLETED_STATUS else 2
    raise Task055FError("superseded_by_task055k_transport_broker")


def _load_current_manifest(root: Path) -> dict[str, Any]:
    return json.loads(_load_current_path(root).read_text(encoding="utf-8"))


def _load_current_path(root: Path) -> Path:
    pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
    path = root / str(pointer.get("manifest") or "")
    if not path.is_file():
        raise Task055FError(f"task055f_current_manifest_missing:{root.name}")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
