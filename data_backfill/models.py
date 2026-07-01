"""Dataclasses for governed A-share backfill runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class BackfillJobStatus:
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    skipped = "skipped"
    resumed = "resumed"
    quarantined = "quarantined"


@dataclass(frozen=True)
class BackfillScope:
    provider: str
    datasets: list[str]
    start_date: str
    end_date: str
    index_codes: list[str]
    security_list_statuses: list[str] = field(default_factory=lambda: ["L"])
    chunk_days: int = 30
    universe_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillJob:
    job_id: str
    dataset: str
    provider: str
    start_date: str | None = None
    end_date: str | None = None
    index_code: str | None = None
    ts_code: str | None = None
    list_status: str | None = None
    estimated_requests: int = 1
    request_budget_group: str = "default"
    status: str = BackfillJobStatus.pending
    records: int = 0
    error: str | None = None
    output_path: str | None = None
    staging_path: str | None = None
    cache_hit_count: int = 0
    audit_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillPlan:
    plan_id: str
    scope: BackfillScope
    jobs: list[BackfillJob]
    dataset_count: int
    job_count: int
    estimated_request_count: int
    expected_artifacts: list[str]
    online_required: bool
    token_required: bool
    max_requests: int | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillRunState:
    plan_id: str
    updated_at: str
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillCoverageRecord:
    dataset: str
    records: int
    unique_keys: int
    duplicate_keys: int
    first_date: str | None
    last_date: str | None
    ts_code_count: int
    status: str
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DatasetCoverageMatrix:
    generated_at: str
    records: list[BackfillCoverageRecord]
    gap_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillQuotaSummary:
    provider: str
    allow_network: bool
    token_present: bool
    token_hash_prefix: str | None
    token_suffix: str | None
    max_requests: int | None
    estimated_requests: int
    status: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillReadinessReport:
    provider: str
    status: str
    online_required: bool
    token_required: bool
    quota: BackfillQuotaSummary
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillRunReport:
    plan_id: str
    provider: str
    status: str
    started_at: str
    finished_at: str
    jobs: list[BackfillJob]
    quota: BackfillQuotaSummary
    coverage: dict[str, Any] | None
    paths: dict[str, str | None]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
