"""Dataclasses for local compute scheduling."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ComputeDeviceType:
    CPU = "cpu"
    CUDA = "cuda"
    MPS = "mps"
    UNKNOWN = "unknown"


class ComputeJobStatus:
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    RESUMED = "resumed"


TERMINAL_STATUSES = {
    ComputeJobStatus.SUCCESS,
    ComputeJobStatus.FAILED,
    ComputeJobStatus.SKIPPED,
    ComputeJobStatus.CANCELLED,
    ComputeJobStatus.TIMED_OUT,
}


class ComputeJobKind:
    SHELL_COMMAND = "shell_command"
    MATRIX_BUILD = "matrix_build"
    FORMULA_CORPUS = "formula_corpus"
    FORMULA_BATCH_EVAL = "formula_batch_eval"
    NEURAL_PRETRAIN = "neural_pretrain"
    FORMULA_SEARCH = "formula_search"
    RESEARCH_SUITE = "research_suite"
    BACKTEST = "backtest"
    BENCHMARK = "benchmark"
    MERGE_RESULTS = "merge_results"


@dataclass(frozen=True)
class ComputeDeviceRecord:
    device_id: str
    device_type: str
    name: str
    index: int | None = None
    uuid: str | None = None
    total_memory_mb: float = 0.0
    free_memory_mb: float = 0.0
    capability: str | None = None
    torch_available: bool = False
    cuda_available: bool = False
    driver_version: str | None = None
    cuda_version: str | None = None
    visible: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComputeResourceSnapshot:
    captured_at: str
    cpu_count: int
    memory_total_mb: float
    memory_available_mb: float
    torch_version: str | None
    cuda_available: bool
    cuda_device_count: int
    devices: list[ComputeDeviceRecord]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["devices"] = [device.to_dict() if hasattr(device, "to_dict") else device for device in self.devices]
        return payload


@dataclass(frozen=True)
class ComputeJobSpec:
    job_id: str
    job_kind: str
    command: list[str]
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    input_paths: list[str] = field(default_factory=list)
    output_dir: str | None = None
    artifact_paths: dict[str, str] = field(default_factory=dict)
    required_device_type: str = ComputeDeviceType.CPU
    gpu_count: int = 0
    cpu_count: int = 1
    memory_mb: int = 0
    max_duration_seconds: float | None = None
    max_retries: int = 0
    priority: int = 0
    dependencies: list[str] = field(default_factory=list)
    shard_id: int | None = None
    shard_count: int | None = None
    data_freeze_id: str | None = None
    data_freeze_dir: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComputeLease:
    lease_id: str
    job_id: str
    device_indices: list[int]
    acquired_at: str
    heartbeat_at: str
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComputeHeartbeat:
    job_id: str
    status: str
    heartbeat_at: str
    lease_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComputeJobRun:
    run_id: str
    job_id: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    return_code: int | None
    attempt: int
    lease_id: str | None = None
    device_indices: list[int] = field(default_factory=list)
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str | None = None
    output_paths: dict[str, str] = field(default_factory=dict)
    redacted_env_count: int = 0
    fallback_to_cpu: bool = False
    physical_devices: list[dict[str, Any]] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    heartbeat_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComputeSchedulerConfig:
    state_dir: str
    output_dir: str
    max_parallel_cpu_jobs: int = 1
    max_parallel_gpu_jobs: int = 1
    fail_fast: bool = False
    dry_run: bool = False
    resume: bool = False
    stale_heartbeat_seconds: float = 3600.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComputeRunReport:
    run_id: str
    created_at: str
    status: str
    resource_snapshot: dict[str, Any]
    job_count: int
    success_count: int
    failed_count: int
    skipped_count: int
    resumed_count: int
    timeout_count: int
    gpu_job_count: int
    cpu_job_count: int
    total_wall_time_seconds: float
    total_gpu_allocated_seconds: float
    average_queue_wait_seconds: float
    average_job_duration_seconds: float
    max_gpu_memory_observed_mb: float
    gpu_count_detected: int
    cuda_available: bool
    fallback_to_cpu_count: int
    oom_error_count: int
    redacted_env_count: int
    paths: dict[str, str]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
