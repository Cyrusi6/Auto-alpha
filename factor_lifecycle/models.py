"""Dataclasses for factor/model lifecycle review."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class LifecyclePolicy:
    min_recent_coverage: float = 0.5
    min_recent_rank_ic: float = -1.0
    max_missing_factor_value_ratio: float = 0.5
    max_staleness_days: int = 30
    max_schema_error_count: int = 0
    max_data_source_error_count: int = 0
    min_execution_fill_rate: float = 0.0
    max_broker_rejected_rate: float = 1.0
    max_capacity_warning_count: int = 999
    max_active_style_exposure_abs: float = 999.0
    max_tracking_error: float = 999.0
    require_schema_validation_passed: bool = False
    require_data_source_ok: bool = False
    require_backtest_metrics: bool = False
    require_review_approval_for_activation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactorHealthCheck:
    name: str
    severity: str
    passed: bool
    value: float | str | bool | None = None
    threshold: float | str | bool | None = None
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactorLifecycleDecision:
    factor_id: str
    model_version_id: str | None
    current_status: str
    recommended_action: str
    severity: str
    reasons: list[str]
    checks: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelReviewPackage:
    model_version_id: str | None
    factor_id: str
    factor_type: str
    lifecycle_status: str
    formula: list[str]
    parent_factor_ids: list[str]
    source_artifacts: dict[str, str]
    key_metrics: dict[str, Any]
    gate_status: str | None
    gate_reasons: list[str]
    promotion_decision: dict[str, Any]
    health_checks: list[dict[str, Any]]
    lifecycle_decision: dict[str, Any]
    reviewer_checklist: list[dict[str, Any]]
    lineage_graph_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LifecycleEvaluationResult:
    factor_id: str
    model_version_id: str | None
    as_of_date: str
    metrics: dict[str, Any]
    checks: list[FactorHealthCheck]
    decision: FactorLifecycleDecision
    policy: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checks"] = [check.to_dict() for check in self.checks]
        payload["decision"] = self.decision.to_dict()
        return payload


@dataclass(frozen=True)
class LifecycleReport:
    created_at: str
    evaluation: dict[str, Any]
    review_package_path: str | None = None
    lineage_graph_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
