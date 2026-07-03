"""Models for research data readiness gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ResearchDataReadinessStatus:
    ready_for_freeze = "ready_for_freeze"
    ready_for_matrix = "ready_for_matrix"
    ready_for_alpha_factory = "ready_for_alpha_factory"
    ready_for_validation = "ready_for_validation"
    not_ready = "not_ready"
    insufficient_data = "insufficient_data"


class DatasetResearchTier:
    core_required = "core_required"
    index_industry_required = "index_industry_required"
    financial_required = "financial_required"
    alpha_optional = "alpha_optional"
    event_optional = "event_optional"
    broker_irrelevant = "broker_irrelevant"


class DatasetPitSafety:
    pit_safe = "pit_safe"
    weak_pit = "weak_pit"
    event_date_only = "event_date_only"
    unsafe_missing_availability = "unsafe_missing_availability"
    unknown = "unknown"


class FeatureReadinessStatus:
    ready = "ready"
    warning = "warning"
    blocked = "blocked"
    missing = "missing"


@dataclass(frozen=True)
class DatasetReadinessCheck:
    dataset: str
    tier: str
    status: str
    severity: str
    record_count: int
    size_bytes: int
    coverage_ratio: float | None
    date_coverage: dict[str, Any]
    ts_code_coverage: dict[str, Any]
    pit_safety: str
    availability_field: str | None
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureFamilyReadiness:
    feature_family: str
    required_datasets: list[str]
    optional_datasets: list[str]
    readiness_status: str
    blockers: list[str] = field(default_factory=list)
    weak_pit_warnings: list[str] = field(default_factory=list)
    future_feature_plan: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchDataReadinessDecision:
    status: str
    core_ready: bool
    expanded_ready: bool
    matrix_ready: bool
    alpha_ready: bool
    validation_ready: bool
    blocker_count: int
    warning_count: int
    required_remediations: list[str]
    recommended_next_commands: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchDataReadinessReport:
    report_id: str
    generated_at: str
    profile_name: str | None
    data_dir: str
    dataset_checks: list[DatasetReadinessCheck]
    feature_readiness: list[FeatureFamilyReadiness]
    decision: ResearchDataReadinessDecision
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "profile_name": self.profile_name,
            "data_dir": self.data_dir,
            "dataset_checks": [item.to_dict() for item in self.dataset_checks],
            "feature_readiness": [item.to_dict() for item in self.feature_readiness],
            "decision": self.decision.to_dict(),
            "summary": dict(self.summary),
        }
