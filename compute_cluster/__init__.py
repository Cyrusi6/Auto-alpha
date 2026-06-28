"""Local compute resource plane for A-share research workloads."""

from .gpu_probe import probe_compute_resources, write_resource_snapshot
from .job_store import LocalComputeJobStore
from .lease import GpuLeaseManager
from .models import (
    ComputeDeviceRecord,
    ComputeDeviceType,
    ComputeHeartbeat,
    ComputeJobKind,
    ComputeJobRun,
    ComputeJobSpec,
    ComputeJobStatus,
    ComputeLease,
    ComputeResourceSnapshot,
    ComputeRunReport,
    ComputeSchedulerConfig,
)
from .runner import run_job
from .scheduler import LocalComputeScheduler

__all__ = [
    "ComputeDeviceRecord",
    "ComputeDeviceType",
    "ComputeHeartbeat",
    "ComputeJobKind",
    "ComputeJobRun",
    "ComputeJobSpec",
    "ComputeJobStatus",
    "ComputeLease",
    "ComputeResourceSnapshot",
    "ComputeRunReport",
    "ComputeSchedulerConfig",
    "GpuLeaseManager",
    "LocalComputeJobStore",
    "LocalComputeScheduler",
    "probe_compute_resources",
    "run_job",
    "write_resource_snapshot",
]
