"""Dataclasses for matrix refresh artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class MatrixRefreshPlan:
    refresh_mode: str
    data_dir: str
    data_freeze_dir: str | None
    matrix_cache_dir: str
    source_hash: str | None
    matrix_hash: str | None
    recommendation: str
    reasons: list[str]
    config: dict[str, Any] = field(default_factory=dict)
    raw_data_index_status: str | None = None
    raw_data_index_hash: str | None = None
    source_dataset_index_count: int = 0
    datasets_missing_index: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MatrixSourceDiff:
    status: str
    source_hash: str | None
    matrix_hash: str | None
    drift_count: int
    issues: list[dict[str, Any]] = field(default_factory=list)
    raw_data_index_status: str | None = None
    raw_data_index_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MatrixFreshnessReport:
    status: str
    matrix_cache_dir: str
    n_stocks: int
    n_dates: int
    source_hash: str | None
    matrix_hash: str | None
    issues: list[dict[str, Any]]
    raw_data_index_status: str | None = None
    raw_data_index_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MatrixRefreshResult:
    status: str
    action: str
    refresh_mode: str
    matrix_cache_dir: str
    source_diff: dict[str, Any]
    freshness: dict[str, Any]
    paths: dict[str, str]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
