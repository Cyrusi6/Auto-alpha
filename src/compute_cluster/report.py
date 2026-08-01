"""Compute run reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .gpu_probe import write_resource_snapshot
from .job_store import LocalComputeJobStore
from .lease import GpuLeaseManager
from .models import ComputeJobStatus, ComputeResourceSnapshot, ComputeRunReport


def write_compute_report(
    run_id: str,
    state_dir: str | Path,
    output_dir: str | Path,
    snapshot: ComputeResourceSnapshot,
    elapsed_seconds: float = 0.0,
) -> ComputeRunReport:
    state = Path(state_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    store = LocalComputeJobStore(state)
    jobs = [job.to_dict() for job in store.list_jobs()]
    runs = store.read_runs()
    job_state = store.load_state().get("jobs", {})
    statuses = [str(row.get("status", ComputeJobStatus.PENDING)) for row in job_state.values()]
    success = statuses.count(ComputeJobStatus.SUCCESS)
    failed = statuses.count(ComputeJobStatus.FAILED)
    skipped = statuses.count(ComputeJobStatus.SKIPPED)
    timed_out = statuses.count(ComputeJobStatus.TIMED_OUT)
    resumed = statuses.count(ComputeJobStatus.RESUMED)
    gpu_jobs = sum(1 for job in jobs if int(job.get("gpu_count", 0) or 0) > 0)
    durations = [float(run.get("duration_seconds", 0.0) or 0.0) for run in runs]
    gpu_seconds = sum(duration * max(1, len(run.get("device_indices", []) or [])) for duration, run in zip(durations, runs))
    fallback = sum(1 for run in runs if run.get("fallback_to_cpu"))
    oom = sum(1 for run in runs if "cuda_oom" in str(run.get("error") or "").lower())
    redacted = sum(int(run.get("redacted_env_count", 0) or 0) for run in runs)
    resource_json, resource_md = write_resource_snapshot(snapshot, output)
    write_jsonl_artifact(output / "compute_jobs.jsonl", jobs, "compute_jobs", "compute_cluster")
    write_jsonl_artifact(output / "compute_job_runs.jsonl", runs, "compute_job_runs", "compute_cluster")
    _copy_if_exists(store.events_path, output / "compute_scheduler_events.jsonl", "compute_scheduler_events")
    _copy_if_exists(store.heartbeats_path, output / "compute_heartbeats.jsonl", "compute_heartbeats")
    leases_path = GpuLeaseManager(state).write_leases_jsonl(output)
    paths = {
        "compute_run_report": str(output / "compute_run_report.json"),
        "compute_run_report_md": str(output / "compute_run_report.md"),
        "compute_resource_snapshot": str(resource_json),
        "compute_resource_snapshot_md": str(resource_md),
        "compute_jobs": str(output / "compute_jobs.jsonl"),
        "compute_job_runs": str(output / "compute_job_runs.jsonl"),
        "compute_scheduler_events": str(output / "compute_scheduler_events.jsonl"),
        "compute_heartbeats": str(output / "compute_heartbeats.jsonl"),
        "gpu_leases": str(leases_path),
    }
    status = "success" if failed == 0 and timed_out == 0 else "failed"
    report = ComputeRunReport(
        run_id=run_id,
        created_at=_utc_now(),
        status=status,
        resource_snapshot=snapshot.to_dict(),
        job_count=len(jobs),
        success_count=success,
        failed_count=failed,
        skipped_count=skipped,
        resumed_count=resumed,
        timeout_count=timed_out,
        gpu_job_count=gpu_jobs,
        cpu_job_count=len(jobs) - gpu_jobs,
        total_wall_time_seconds=float(elapsed_seconds),
        total_gpu_allocated_seconds=float(gpu_seconds),
        average_queue_wait_seconds=0.0,
        average_job_duration_seconds=float(sum(durations) / len(durations)) if durations else 0.0,
        max_gpu_memory_observed_mb=max((device.free_memory_mb for device in snapshot.devices), default=0.0),
        gpu_count_detected=int(snapshot.cuda_device_count),
        cuda_available=bool(snapshot.cuda_available),
        fallback_to_cpu_count=fallback,
        oom_error_count=oom,
        redacted_env_count=redacted,
        paths=paths,
        warnings=snapshot.warnings,
    )
    write_json_artifact(output / "compute_run_report.json", report.to_dict(), "compute_run_report", "compute_cluster")
    (output / "compute_run_report.md").write_text(_render_report(report), encoding="utf-8")
    return report


def _copy_if_exists(source: Path, target: Path, artifact_type: str) -> None:
    records = []
    if source.exists():
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    write_jsonl_artifact(target, records, artifact_type, "compute_cluster")


def _render_report(report: ComputeRunReport) -> str:
    return "\n".join(
        [
            "# Compute Run Report",
            "",
            f"- status: `{report.status}`",
            f"- jobs: {report.job_count}",
            f"- success: {report.success_count}",
            f"- failed: {report.failed_count}",
            f"- cuda_available: `{report.cuda_available}`",
            f"- gpu_count_detected: {report.gpu_count_detected}",
            f"- fallback_to_cpu_count: {report.fallback_to_cpu_count}",
        ]
    ) + "\n"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
