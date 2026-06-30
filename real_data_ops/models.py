"""Dataclasses for real data production operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class RealDataRunMode:
    offline_sample = "offline_sample"
    fake_tushare = "fake_tushare"
    online_tushare_smoke = "online_tushare_smoke"
    online_tushare_full_backfill = "online_tushare_full_backfill"
    online_tushare_incremental = "online_tushare_incremental"


class RealDataRunStatus:
    planned = "planned"
    running = "running"
    success = "success"
    warning = "warning"
    blocked = "blocked"
    failed = "failed"
    skipped = "skipped"


@dataclass(frozen=True)
class RealDataProfile:
    profile_id: str
    profile_name: str
    provider: str
    api_url: str | None
    datasets: list[str]
    start_date: str
    end_date: str
    index_codes: list[str]
    security_list_statuses: list[str]
    chunk_strategy: str = "uniform"
    dataset_chunk_days: dict[str, int] = field(default_factory=dict)
    max_requests: int | None = None
    rate_limit_per_minute: int = 150
    require_token: bool = False
    allow_network: bool = False
    mode: str = RealDataRunMode.offline_sample
    storage_mode: str = "append"
    freeze_mode: str = "copy"
    matrix_refresh_mode: str = "skip_if_fresh"
    priority: int = 100
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RealDataReadinessReport:
    status: str
    provider: str
    profile_name: str
    allow_network: bool
    require_token: bool
    token: dict[str, Any]
    api_url_host: str
    estimated_requests: int
    estimated_min_runtime_minutes: float
    diagnostics: list[dict[str, Any]]
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RealDataSlaCheck:
    check_id: str
    status: str
    value: Any
    threshold: Any = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RealDataSlaReport:
    status: str
    checks: list[RealDataSlaCheck]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "checks": [check.to_dict() for check in self.checks], "summary": self.summary}


@dataclass(frozen=True)
class RealDataSizeReport:
    data_dir: str
    matrix_cache_dir: str | None
    freeze_dir: str | None
    total_size_bytes: int
    total_size_gb: float
    dataset_size_bytes: dict[str, int]
    dataset_record_count: dict[str, int]
    avg_record_size_bytes: dict[str, float]
    matrix_cache_size_bytes: int
    freeze_size_bytes: int
    staging_size_bytes: int
    cache_size_bytes: int
    largest_files: list[dict[str, Any]]
    estimated_full_size_if_partial: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RealDataPipelineRun:
    run_id: str
    profile: dict[str, Any]
    status: str
    started_at: str
    finished_at: str
    summary: dict[str, Any]
    paths: dict[str, str | None]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RealDataRunbook:
    profile_name: str
    plan_path: str | None
    state_path: str | None
    staging_dir: str | None
    data_dir: str
    completed_jobs: int
    failed_jobs: int
    quarantined_jobs: int
    estimated_requests: int
    request_budget_used: int
    rate_limit_per_minute: int
    estimated_min_runtime_minutes: float
    token_expiry: str | None
    time_to_expiry_hours: float | None
    expiry_risk: str
    resume_command: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
