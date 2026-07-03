"""Models for raw data landing QA."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class RawDatasetLandingStatus:
    complete = "complete"
    partial = "partial"
    missing = "missing"
    warning = "warning"
    failed = "failed"


@dataclass(frozen=True)
class RawDatasetLandingCheck:
    dataset: str
    status: str
    records_path: str
    exists: bool
    size_bytes: int
    line_count: int
    parse_error_count: int
    first_date: str | None
    last_date: str | None
    ts_code_count: int
    duplicate_key_estimate: int
    null_or_empty_field_count: int
    expected_records_hint: int | None = None
    coverage_ratio: float | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawDatasetCoverageRow:
    dataset: str
    coverage_type: str
    expected_units: int | None
    observed_units: int
    coverage_ratio: float | None
    status: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawFreezeReadinessDecision:
    decision_id: str
    status: str
    blocker_count: int
    warning_count: int
    blockers: list[str]
    warnings: list[str]
    checks: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawDataLandingReport:
    report_id: str
    generated_at: str
    profile_name: str | None
    data_dir: str
    datasets: list[RawDatasetLandingCheck]
    coverage_matrix: list[RawDatasetCoverageRow]
    freeze_readiness: RawFreezeReadinessDecision
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "profile_name": self.profile_name,
            "data_dir": self.data_dir,
            "datasets": [item.to_dict() for item in self.datasets],
            "coverage_matrix": [item.to_dict() for item in self.coverage_matrix],
            "freeze_readiness": self.freeze_readiness.to_dict(),
            "summary": dict(self.summary),
        }
