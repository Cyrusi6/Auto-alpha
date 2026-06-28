"""Sequential local compute scheduler with GPU leases and retries."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from .gpu_probe import probe_compute_resources
from .job_store import LocalComputeJobStore
from .lease import GpuLeaseManager
from .models import ComputeDeviceType, ComputeJobSpec, ComputeJobStatus, ComputeSchedulerConfig
from .report import write_compute_report
from .runner import release_job_lease, run_job


class LocalComputeScheduler:
    def __init__(self, config: ComputeSchedulerConfig):
        self.config = config
        self.store = LocalComputeJobStore(config.state_dir)
        self.lease_manager = GpuLeaseManager(config.state_dir)

    def submit_jobs(self, jobs: list[ComputeJobSpec]) -> dict[str, int]:
        return self.store.submit_jobs(jobs)

    def run(self):
        if self.config.resume:
            self.store.resume_pending_or_failed()
        run_id = f"compute_{uuid.uuid4().hex[:12]}"
        start = time.perf_counter()
        while True:
            runnable = [job for job in self.store.runnable_jobs(resume=self.config.resume) if self.store.dependencies_satisfied(job)]
            runnable = [job for job in runnable if self.store.attempts(job.job_id) <= job.max_retries]
            if not runnable:
                break
            progressed = False
            for job in runnable:
                status = self.store.job_status(job.job_id)
                if status == ComputeJobStatus.SUCCESS:
                    continue
                lease = None
                if job.required_device_type == ComputeDeviceType.CUDA and job.gpu_count > 0:
                    lease = self.lease_manager.acquire_gpu_lease(job.job_id, required_gpus=job.gpu_count)
                    if lease is None:
                        self.store.update_status(job.job_id, ComputeJobStatus.SKIPPED, error="gpu_unavailable")
                        progressed = True
                        continue
                    self.store.update_status(job.job_id, ComputeJobStatus.LEASED, lease_id=lease.lease_id)
                run = run_job(job, self.store, self.config.output_dir, lease=lease, dry_run=self.config.dry_run)
                release_job_lease(self.lease_manager, lease)
                progressed = True
                if run.status in {ComputeJobStatus.FAILED, ComputeJobStatus.TIMED_OUT}:
                    if self.store.attempts(job.job_id) <= job.max_retries:
                        self.store.update_status(job.job_id, ComputeJobStatus.PENDING, retry_after_failure=True)
                    elif self.config.fail_fast:
                        return self._write_report(run_id, start)
            if not progressed:
                break
        return self._write_report(run_id, start)

    def _write_report(self, run_id: str, started: float):
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        snapshot = probe_compute_resources()
        return write_compute_report(
            run_id=run_id,
            state_dir=self.config.state_dir,
            output_dir=output_dir,
            snapshot=snapshot,
            elapsed_seconds=float(time.perf_counter() - started),
        )
