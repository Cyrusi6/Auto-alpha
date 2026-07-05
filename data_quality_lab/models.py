"""Dataclasses for semantic data quality reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field as dc_field
from typing import Any


class DataQualitySeverity:
    info = "info"
    warning = "warning"
    error = "error"
    blocker = "blocker"


class DataQualityRuleScope:
    dataset = "dataset"
    cross_dataset = "cross_dataset"
    freeze_gate = "freeze_gate"
    feature_gate = "feature_gate"
    matrix_gate = "matrix_gate"


@dataclass(frozen=True)
class DataQualityRuleDefinition:
    rule_id: str
    dataset: str
    name: str
    scope: str
    severity: str
    description: str
    suggestion_action: str
    enabled: bool = True
    metadata: dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataQualityIssue:
    issue_id: str
    rule_id: str
    dataset: str
    severity: str
    message: str
    key: str | None = None
    field: str | None = None
    sample: dict[str, Any] = dc_field(default_factory=dict)
    repair_action: str | None = None
    metadata: dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DatasetQualitySummary:
    dataset: str
    record_count: int
    rule_count: int
    issue_count: int
    blocker_count: int
    error_count: int
    warning_count: int
    info_count: int
    status: str
    first_date: str | None = None
    last_date: str | None = None
    ts_code_count: int = 0
    sample_issue_ids: list[str] = dc_field(default_factory=list)
    metadata: dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataQualityScorecard:
    status: str
    dataset_count: int
    issue_count: int
    blocker_count: int
    error_count: int
    warning_count: int
    info_count: int
    dataset_summaries: list[DatasetQualitySummary]
    severity_distribution: dict[str, int]
    rule_distribution: dict[str, int]
    created_at: str
    metadata: dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["dataset_summaries"] = [item.to_dict() for item in self.dataset_summaries]
        return payload


@dataclass(frozen=True)
class DataQualityRepairSuggestion:
    suggestion_id: str
    dataset: str
    issue_id: str
    rule_id: str
    severity: str
    action: str
    command_hint: str
    automatic: bool
    reason: str
    metadata: dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataQualityFreezeGate:
    status: str
    can_create_freeze: bool
    can_build_matrix: bool
    can_run_core_alpha: bool
    can_run_expanded_alpha: bool
    blocker_count: int
    core_blocker_count: int
    expanded_blocker_count: int
    recommended_next_action: str
    reasons: list[str]
    created_at: str
    metadata: dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataQualityLabReport:
    report_id: str
    status: str
    profile_name: str | None
    data_dir: str
    start_date: str | None
    end_date: str | None
    scorecard: DataQualityScorecard
    freeze_gate: DataQualityFreezeGate
    cross_dataset_report: dict[str, Any]
    paths: dict[str, str]
    summary: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "status": self.status,
            "profile_name": self.profile_name,
            "data_dir": self.data_dir,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "scorecard": self.scorecard.to_dict(),
            "freeze_gate": self.freeze_gate.to_dict(),
            "cross_dataset_report": dict(self.cross_dataset_report),
            "paths": dict(self.paths),
            "summary": dict(self.summary),
            "created_at": self.created_at,
        }
