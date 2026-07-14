"""Subprocess runner for local compute jobs."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .heartbeat import write_heartbeat
from .job_store import LocalComputeJobStore
from .lease import GpuLeaseManager
from .models import ComputeDeviceType, ComputeJobRun, ComputeJobSpec, ComputeJobStatus, ComputeLease


SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD")


def run_job(
    job: ComputeJobSpec,
    store: LocalComputeJobStore,
    output_dir: str | Path,
    lease: ComputeLease | None = None,
    dry_run: bool = False,
) -> ComputeJobRun:
    started_at = _utc_now()
    start = time.perf_counter()
    output = Path(output_dir)
    job_output = output / "jobs" / job.job_id
    job_output.mkdir(parents=True, exist_ok=True)
    attempt = store.increment_attempt(job.job_id)
    store.update_status(job.job_id, ComputeJobStatus.RUNNING, attempt=attempt, lease_id=lease.lease_id if lease else None)
    write_heartbeat(store, job.job_id, ComputeJobStatus.RUNNING, lease.lease_id if lease else None)
    stdout_tail = ""
    stderr_tail = ""
    error = None
    return_code = 0
    status = ComputeJobStatus.SUCCESS
    env, redacted_env_count, fallback_to_cpu = _build_env(job, lease)
    command = list(job.command) + list(job.args)
    try:
        if dry_run:
            stdout_tail = "dry_run"
        elif not command:
            raise ValueError("job command is empty")
        else:
            stdout_log = job_output / "stdout.log"
            stderr_log = job_output / "stderr.log"
            with stdout_log.open("w", encoding="utf-8") as stdout_handle, stderr_log.open("w", encoding="utf-8") as stderr_handle:
                proc = subprocess.Popen(command, cwd=job.cwd or None, env=env, text=True, stdout=stdout_handle, stderr=stderr_handle)
                heartbeat_manager = GpuLeaseManager(store.state_dir)
                last_heartbeat = 0.0
                while proc.poll() is None:
                    elapsed = time.perf_counter() - start
                    if job.max_duration_seconds is not None and elapsed > job.max_duration_seconds:
                        proc.kill(); proc.wait()
                        raise subprocess.TimeoutExpired(command, job.max_duration_seconds)
                    if elapsed - last_heartbeat >= 15.0:
                        write_heartbeat(store, job.job_id, ComputeJobStatus.RUNNING, lease.lease_id if lease else None, elapsed_seconds=elapsed)
                        if lease is not None: heartbeat_manager.heartbeat(lease.lease_id)
                        last_heartbeat = elapsed
                    try: proc.wait(timeout=0.25)
                    except subprocess.TimeoutExpired: pass
            stdout = stdout_log.read_text(encoding="utf-8", errors="replace")
            stderr = stderr_log.read_text(encoding="utf-8", errors="replace")
            return_code = int(proc.returncode)
            stdout_tail = _tail(stdout)
            stderr_tail = _tail(stderr)
            if return_code != 0:
                status = ComputeJobStatus.FAILED
                error = stderr_tail or f"return_code={return_code}"
            if "out of memory" in (stderr_tail + stdout_tail).lower() or "cuda oom" in (stderr_tail + stdout_tail).lower():
                error = "cuda_oom"
    except subprocess.TimeoutExpired as exc:
        return_code = None
        status = ComputeJobStatus.TIMED_OUT
        stdout_tail = _tail(exc.stdout or "")
        stderr_tail = _tail(exc.stderr or "")
        error = "timed_out"
    except Exception as exc:
        return_code = None
        status = ComputeJobStatus.FAILED
        error = str(exc)
    finished_at = _utc_now()
    duration = float(time.perf_counter() - start)
    (job_output / "stdout_tail.txt").write_text(stdout_tail, encoding="utf-8")
    (job_output / "stderr_tail.txt").write_text(stderr_tail, encoding="utf-8")
    run = ComputeJobRun(
        run_id=f"run_{uuid.uuid4().hex[:16]}",
        job_id=job.job_id,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration,
        return_code=return_code,
        attempt=attempt,
        lease_id=lease.lease_id if lease else None,
        device_indices=lease.device_indices if lease else [],
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        error=error,
        output_paths={
            "stdout_tail": str(job_output / "stdout_tail.txt"),
            "stderr_tail": str(job_output / "stderr_tail.txt"),
            "job_run": str(job_output / f"compute_job_run_{job.job_id}.json"),
        },
        redacted_env_count=redacted_env_count,
        fallback_to_cpu=fallback_to_cpu,
    )
    write_json_artifact(job_output / f"compute_job_run_{job.job_id}.json", run.to_dict(), "compute_job_run", "compute_cluster")
    store.append_run(run.to_dict())
    store.update_status(job.job_id, status, return_code=return_code, error=error)
    write_heartbeat(store, job.job_id, status, lease.lease_id if lease else None)
    return run


def release_job_lease(lease_manager: GpuLeaseManager, lease: ComputeLease | None) -> None:
    if lease is not None:
        lease_manager.release_lease(lease.lease_id)


def _build_env(job: ComputeJobSpec, lease: ComputeLease | None) -> tuple[dict[str, str], int, bool]:
    env = os.environ.copy()
    redacted = 0
    for key, value in job.env.items():
        env[str(key)] = str(value)
        if any(marker in str(key).upper() for marker in SECRET_MARKERS):
            redacted += 1
    fallback = False
    if job.required_device_type == ComputeDeviceType.CUDA and lease is not None:
        env["CUDA_VISIBLE_DEVICES"] = ",".join(str(index) for index in lease.device_indices)
    elif job.required_device_type == ComputeDeviceType.CUDA and lease is None:
        env["CUDA_VISIBLE_DEVICES"] = ""
        fallback = True
    return env, redacted, fallback


def _tail(text: str, max_chars: int = 4000) -> str:
    return text[-max_chars:] if len(text) > max_chars else text


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
