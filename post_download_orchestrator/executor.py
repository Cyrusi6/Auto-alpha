"""Stateful post-download execution in safe local mode."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from artifact_schema.writer import utc_now

from .models import FreezeCandidatePackage, PostDownloadPlan, PostDownloadState, PostDownloadStep, PostDownloadStepRun


MUTATION_STEPS = {
    "compact",
    "data_lake_create_version",
    "data_lake_promote_candidate",
    "data_lake_create_freeze",
    "freeze_validate",
    "matrix_refresh_execute",
    "matrix_freshness_validate",
    "final_package",
    "research_suite_dry_smoke",
}


def execute_post_download_plan(
    plan: PostDownloadPlan,
    *,
    data_dir: str | Path,
    run_dir: str | Path | None,
    output_dir: str | Path,
    matrix_cache_dir: str | Path | None,
    readiness_report_path: str | Path | None,
    execute: bool = False,
    resume: bool = False,
    allow_incomplete: bool = False,
    allow_real_data_path: bool = False,
    start_at_step: str | None = None,
    stop_after_step: str | None = None,
    refresh_step: str | None = None,
) -> tuple[list[PostDownloadStepRun], PostDownloadState, FreezeCandidatePackage | None, list[dict[str, Any]]]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "post_download_state.json"
    run_id = f"post_download_run_{utc_now().replace(':', '').replace('-', '')}"
    state = _load_state(state_path, run_id, plan.plan_id)
    step_runs: list[PostDownloadStepRun] = []
    events: list[dict[str, Any]] = []
    package: FreezeCandidatePackage | None = None
    started = start_at_step is None
    real_path_blocker = _real_path_blocker(data_dir, allow_real_data_path)
    for step in plan.steps:
        if not started:
            started = step.step_id == start_at_step
            if not started:
                continue
        previous = state.steps.get(step.step_id)
        if resume and previous and previous.get("status") == "success" and refresh_step != step.step_id:
            run = _step_run(step, "resumed", output_artifacts=list(previous.get("output_artifacts") or []), summary={"resumed": True})
            step_runs.append(run)
            events.append(_event(step.step_id, "resumed"))
            continue
        if step.blocked and not allow_incomplete:
            run = _step_run(step, "blocked", blocker_reason=step.reason or "step is blocked by readiness")
            step_runs.append(run)
            state = _mark(state, run)
            events.append(_event(step.step_id, "blocked", run.blocker_reason))
            break
        if real_path_blocker and step.step_id in MUTATION_STEPS:
            run = _step_run(step, "blocked", blocker_reason=real_path_blocker)
            step_runs.append(run)
            state = _mark(state, run)
            events.append(_event(step.step_id, "blocked", run.blocker_reason))
            break
        if not execute:
            run = _step_run(step, "skipped", summary={"plan_only": True})
        elif allow_incomplete and step.step_id in MUTATION_STEPS:
            run = _step_run(step, "skipped", blocker_reason="allow-incomplete only permits diagnostic steps")
        else:
            run, package = _execute_local_step(step, root, data_dir, run_dir, matrix_cache_dir, readiness_report_path, package)
        step_runs.append(run)
        state = _mark(state, run)
        events.append(_event(step.step_id, run.status, run.error or run.blocker_reason or ""))
        if stop_after_step == step.step_id:
            break
    _save_state(state, state_path)
    _append_events(root / "post_download_events.jsonl", events)
    return step_runs, state, package, events


def build_freeze_candidate_package(
    *,
    data_dir: str | Path,
    run_dir: str | Path | None,
    output_dir: str | Path,
    matrix_cache_dir: str | Path | None,
    readiness_report_path: str | Path | None,
    proposed_freeze_name: str,
) -> FreezeCandidatePackage:
    readiness = _read_json(readiness_report_path)
    decision = readiness.get("decision", {}) if isinstance(readiness.get("decision"), dict) else readiness
    status = str(decision.get("status") or readiness.get("status") or "missing_readiness")
    blockers = list(decision.get("required_remediations") or [])
    core_ready = bool(decision.get("core_ready", False))
    failed_quarantined = _failed_summary(readiness)
    package_status = "approved_candidate" if core_ready and not blockers and not failed_quarantined.get("core_failed_or_quarantined", 0) else "blocked_candidate"
    if status in {"raw_download_in_progress", "raw_download_complete_but_needs_repair", "not_ready", "insufficient_data"}:
        package_status = "blocked_candidate"
    return FreezeCandidatePackage(
        package_id=f"freeze_candidate_{utc_now().replace(':', '').replace('-', '')}",
        created_at=utc_now(),
        status=package_status,
        data_dir=str(data_dir),
        run_dir=str(run_dir) if run_dir else None,
        proposed_freeze_name=proposed_freeze_name,
        proposed_matrix_cache_dir=str(matrix_cache_dir) if matrix_cache_dir else None,
        observer_report_path=str(Path(output_dir) / "observer_latest" / "backfill_observer_report.json"),
        raw_landing_report_path=str(Path(output_dir) / "raw_landing_latest" / "raw_data_landing_report.json"),
        repair_report_path=str(Path(output_dir) / "repair" / "repair_run_report.json"),
        research_readiness_report_path=str(readiness_report_path) if readiness_report_path else None,
        dataset_progress_summary=_dataset_progress_summary(readiness),
        dataset_size_summary={"data_size_gb": (readiness.get("summary") or {}).get("data_size_gb", 0.0) if isinstance(readiness.get("summary"), dict) else 0.0},
        failed_quarantined_summary=failed_quarantined,
        pit_safety_summary={
            "weak_pit_dataset_count": (readiness.get("summary") or {}).get("weak_pit_dataset_count", 0) if isinstance(readiness.get("summary"), dict) else 0,
            "unsafe_pit_dataset_count": (readiness.get("summary") or {}).get("unsafe_pit_dataset_count", 0) if isinstance(readiness.get("summary"), dict) else 0,
        },
        proposed_dataset_version_metadata={"readiness_status": status, "core_ready": core_ready},
        blockers=blockers,
        warnings=list(decision.get("warnings") or []),
        recommended_next_command=(decision.get("recommended_next_commands") or [None])[0] if isinstance(decision.get("recommended_next_commands"), list) else None,
    )


def _execute_local_step(
    step: PostDownloadStep,
    root: Path,
    data_dir: str | Path,
    run_dir: str | Path | None,
    matrix_cache_dir: str | Path | None,
    readiness_report_path: str | Path | None,
    package: FreezeCandidatePackage | None,
) -> tuple[PostDownloadStepRun, FreezeCandidatePackage | None]:
    out = root / "step_artifacts" / step.step_id
    out.mkdir(parents=True, exist_ok=True)
    artifacts: list[str] = []
    status = "success"
    summary: dict[str, Any] = {"local_state_machine": True}
    if step.step_id == "final_package":
        from artifact_schema.writer import write_json_artifact

        package = build_freeze_candidate_package(
            data_dir=data_dir,
            run_dir=run_dir,
            output_dir=root,
            matrix_cache_dir=matrix_cache_dir,
            readiness_report_path=readiness_report_path,
            proposed_freeze_name="research_data_freeze",
        )
        path = out / "freeze_candidate_package.json"
        write_json_artifact(path, package.to_dict(), "freeze_candidate_package", "post_download_orchestrator")
        artifacts.append(str(path))
        status = "warning" if package.status == "blocked_candidate" else "success"
        summary["freeze_candidate_status"] = package.status
    else:
        marker = out / "post_download_step_result.json"
        marker.write_text(json.dumps({"step_id": step.step_id, "status": "success", "created_at": utc_now()}, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts.append(str(marker))
    return _step_run(step, status, output_artifacts=artifacts, summary=summary), package


def _step_run(
    step: PostDownloadStep,
    status: str,
    *,
    output_artifacts: list[str] | None = None,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
    blocker_reason: str | None = None,
) -> PostDownloadStepRun:
    now = utc_now()
    return PostDownloadStepRun(
        step_id=step.step_id,
        status=status,
        started_at=now,
        ended_at=now,
        command=step.command,
        output_artifacts=output_artifacts or [],
        summary=summary or {},
        error=error,
        blocker_reason=blocker_reason,
        resume_policy=step.resume_policy,
    )


def _real_path_blocker(data_dir: str | Path, allow_real_data_path: bool) -> str | None:
    try:
        resolved = Path(data_dir).resolve()
    except OSError:
        resolved = Path(data_dir)
    configured = os.environ.get("ASHARE_REAL_DATA_ROOT_PREFIX") or os.environ.get("ASHARE_REAL_DATA_ROOT")
    if not configured:
        return None
    real_data_prefix = Path(configured).expanduser().resolve()
    if (resolved == real_data_prefix or real_data_prefix in resolved.parents) and not allow_real_data_path:
        return "real data mutation steps require --allow-real-data-path"
    return None


def _load_state(path: Path, run_id: str, plan_id: str) -> PostDownloadState:
    if not path.exists():
        return PostDownloadState(run_id=run_id, plan_id=plan_id, updated_at=utc_now(), steps={})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return PostDownloadState(run_id=run_id, plan_id=plan_id, updated_at=utc_now(), steps={})
    return PostDownloadState(run_id=str(payload.get("run_id") or run_id), plan_id=str(payload.get("plan_id") or plan_id), updated_at=str(payload.get("updated_at") or ""), steps=dict(payload.get("steps") or {}))


def _save_state(state: PostDownloadState, path: Path) -> None:
    from artifact_schema.writer import write_json_artifact

    write_json_artifact(path, state.to_dict(), "post_download_state", "post_download_orchestrator")


def _mark(state: PostDownloadState, run: PostDownloadStepRun) -> PostDownloadState:
    steps = dict(state.steps)
    steps[run.step_id] = run.to_dict()
    return PostDownloadState(run_id=state.run_id, plan_id=state.plan_id, updated_at=utc_now(), steps=steps)


def _event(step_id: str, status: str, message: str | None = None) -> dict[str, Any]:
    return {"created_at": utc_now(), "step_id": step_id, "status": status, "message": message or ""}


def _append_events(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path or not Path(path).exists():
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _failed_summary(readiness: dict[str, Any]) -> dict[str, Any]:
    summary = readiness.get("summary") if isinstance(readiness.get("summary"), dict) else {}
    failed = int(summary.get("failed_job_count", 0) or 0)
    quarantined = int(summary.get("quarantined_job_count", 0) or 0)
    return {"failed_jobs": failed, "quarantined_jobs": quarantined, "core_failed_or_quarantined": failed + quarantined}


def _dataset_progress_summary(readiness: dict[str, Any]) -> dict[str, Any]:
    rows = readiness.get("dataset_checks", []) if isinstance(readiness.get("dataset_checks"), list) else []
    return {
        "dataset_count": len(rows),
        "blocked_dataset_count": sum(1 for row in rows if isinstance(row, dict) and row.get("status") == "blocked"),
        "warning_dataset_count": sum(1 for row in rows if isinstance(row, dict) and row.get("status") == "warning"),
    }
