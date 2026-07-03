"""Run repair batches with explicit execution and resume state."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from artifact_schema.writer import utc_now

from .models import BackfillRepairBatchPlan, BackfillRepairJob, BackfillRepairRunReport, BackfillRepairRunState, RepairJobStatus


REAL_DATA_PREFIX = Path("/home/lijunsi/data").resolve()


def run_repair_batch(
    plan: BackfillRepairBatchPlan,
    *,
    output_dir: str | Path,
    execute: bool = False,
    resume: bool = False,
    run_commands: bool = False,
    allow_network: bool = False,
    allow_real_data_path: bool = False,
) -> BackfillRepairRunReport:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "repair_run_state.json"
    state = _load_state(state_path, plan.repair_batch_id)
    results: list[BackfillRepairJob] = []
    events: list[dict[str, Any]] = []
    blocked_reason = _safety_blocker(plan, allow_network=allow_network, allow_real_data_path=allow_real_data_path)
    for job in plan.jobs:
        existing = state.jobs.get(job.repair_job_id)
        if resume and existing and existing.get("status") == RepairJobStatus.success:
            resumed = replace(job, status=RepairJobStatus.resumed, records=int(existing.get("records", 0) or 0))
            results.append(resumed)
            events.append(_event(job, "resumed", "previous success reused"))
            continue
        if blocked_reason:
            blocked = replace(job, status=RepairJobStatus.blocked, error=blocked_reason)
            results.append(blocked)
            state = _mark(state, blocked)
            events.append(_event(job, "blocked", blocked_reason))
            continue
        if not execute:
            dry = replace(job, status=RepairJobStatus.dry_run, error="dry_run")
            results.append(dry)
            state = _mark(state, dry)
            events.append(_event(job, "dry_run", "command not executed"))
            continue
        if run_commands:
            result = _run_command(job)
        else:
            result = replace(job, status=RepairJobStatus.success, metadata={**job.metadata, "simulated": True})
        results.append(result)
        state = _mark(state, result)
        events.append(_event(job, result.status, result.error or ""))
    _save_state(state, state_path)
    _append_jsonl(root / "repair_events.jsonl", events)
    status = _status(results, blocked_reason)
    report = BackfillRepairRunReport(
        repair_run_id=f"repair_run_{utc_now().replace(':', '').replace('-', '')}",
        created_at=utc_now(),
        mode="execute" if execute else "dry_run",
        status=status,
        plan=plan,
        job_results=results,
        summary={
            "repair_job_count": len(results),
            "success_jobs": sum(job.status == RepairJobStatus.success for job in results),
            "resumed_jobs": sum(job.status == RepairJobStatus.resumed for job in results),
            "dry_run_jobs": sum(job.status == RepairJobStatus.dry_run for job in results),
            "failed_jobs": sum(job.status == RepairJobStatus.failed for job in results),
            "blocked_jobs": sum(job.status == RepairJobStatus.blocked for job in results),
            "blocked_reason": blocked_reason,
        },
    )
    return report


def _run_command(job: BackfillRepairJob) -> BackfillRepairJob:
    completed = subprocess.run(job.command, shell=True, text=True, capture_output=True, timeout=600)
    if completed.returncode == 0:
        return replace(job, status=RepairJobStatus.success, metadata={**job.metadata, "stdout_tail": completed.stdout[-2000:]})
    return replace(job, status=RepairJobStatus.failed, error=completed.stderr[-2000:] or completed.stdout[-2000:] or f"returncode={completed.returncode}")


def _safety_blocker(plan: BackfillRepairBatchPlan, *, allow_network: bool, allow_real_data_path: bool) -> str | None:
    reasons: list[str] = []
    data_path = Path(plan.data_dir)
    try:
        resolved = data_path.resolve()
    except OSError:
        resolved = data_path
    if str(resolved).startswith(str(REAL_DATA_PREFIX)) and not allow_real_data_path:
        reasons.append("real data paths require --allow-real-data-path")
    joined = "\n".join(job.command for job in plan.jobs)
    if ".env.local" in joined:
        reasons.append("commands referencing .env.local must be reviewed and executed manually")
    if "--allow-network" in joined and not allow_network:
        reasons.append("network repair commands require --allow-network")
    return "; ".join(reasons) if reasons else None


def _status(results: list[BackfillRepairJob], blocked_reason: str | None) -> str:
    if blocked_reason:
        return "blocked"
    if any(job.status == RepairJobStatus.failed for job in results):
        return "failed"
    if any(job.status == RepairJobStatus.dry_run for job in results):
        return "dry_run"
    return "success"


def _event(job: BackfillRepairJob, status: str, message: str = "") -> dict[str, Any]:
    return {"created_at": utc_now(), "repair_job_id": job.repair_job_id, "dataset": job.dataset, "status": status, "message": message}


def _load_state(path: Path, repair_batch_id: str) -> BackfillRepairRunState:
    if not path.exists():
        return BackfillRepairRunState(repair_batch_id=repair_batch_id, updated_at=utc_now(), jobs={})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return BackfillRepairRunState(repair_batch_id=repair_batch_id, updated_at=utc_now(), jobs={})
    return BackfillRepairRunState(repair_batch_id=str(payload.get("repair_batch_id") or repair_batch_id), updated_at=str(payload.get("updated_at") or ""), jobs=dict(payload.get("jobs") or {}))


def _save_state(state: BackfillRepairRunState, path: Path) -> None:
    from artifact_schema.writer import write_json_artifact

    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_artifact(path, state.to_dict(), "backfill_repair_state", "backfill_repair")


def _mark(state: BackfillRepairRunState, job: BackfillRepairJob) -> BackfillRepairRunState:
    jobs = dict(state.jobs)
    jobs[job.repair_job_id] = job.to_dict()
    return BackfillRepairRunState(repair_batch_id=state.repair_batch_id, updated_at=utc_now(), jobs=jobs)


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
