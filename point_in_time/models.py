"""Dataclasses for point-in-time data governance artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class DatasetAvailabilityTiming:
    before_market_open = "before_market_open"
    after_market_close = "after_market_close"
    next_trade_day_open = "next_trade_day_open"
    announced_date = "announced_date"
    effective_date = "effective_date"
    unknown = "unknown"


@dataclass(frozen=True)
class PITDatasetContract:
    dataset: str
    date_field: str | None
    entity_field: str | None
    availability_date_field: str | None
    effective_date_field: str | None
    required_fields: list[str]
    max_allowed_lag_days: int | None
    timing: str
    allow_forward_fill: bool
    point_in_time_safe_by_default: bool
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataAvailabilityRecord:
    dataset: str
    entity: str
    event_date: str
    availability_date: str
    available: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SecurityLifecycleRecord:
    ts_code: str
    symbol: str
    name: str
    list_date: str
    delist_date: str | None
    list_status: str
    is_st: bool
    exchange: str | None = None
    board: str | None = None
    industry: str | None = None
    area: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActiveSecurityMask:
    ts_code: str
    trade_date: str
    is_active: bool
    reason: str
    listing_age_days: int
    list_status: str
    is_st: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PITValidationIssue:
    severity: str
    code: str
    message: str
    dataset: str | None = None
    key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PITValidationReport:
    generated_at: str
    data_dir: str
    as_of_date: str | None
    feature_cutoff_mode: str
    issues: list[PITValidationIssue]
    dataset_summaries: dict[str, dict[str, Any]]
    security_status_distribution: dict[str, int]
    active_universe_coverage: float

    @property
    def blocker_count(self) -> int:
        return sum(issue.severity == "blocker" for issue in self.issues)

    @property
    def error_count(self) -> int:
        return sum(issue.severity == "error" for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity == "warning" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "data_dir": self.data_dir,
            "as_of_date": self.as_of_date,
            "feature_cutoff_mode": self.feature_cutoff_mode,
            "blocker_count": self.blocker_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": [issue.to_dict() for issue in self.issues],
            "dataset_summaries": self.dataset_summaries,
            "security_status_distribution": self.security_status_distribution,
            "active_universe_coverage": float(self.active_universe_coverage),
            "status": "failed" if self.blocker_count or self.error_count else ("warning" if self.warning_count else "passed"),
        }


@dataclass(frozen=True)
class SurvivorshipBiasReport:
    generated_at: str
    data_dir: str
    current_only_security_master: bool
    securities_total: int
    listed_count: int
    delisted_count: int
    paused_count: int
    warning_count: int
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PITDatasetManifest:
    generated_at: str
    data_dir: str
    datasets: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
