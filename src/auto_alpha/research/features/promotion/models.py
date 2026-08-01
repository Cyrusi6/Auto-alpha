"""Dataclasses for feature promotion review and policy artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class FeaturePromotionStatus:
    report_only = "report_only"
    alpha_eligible = "alpha_eligible"
    risk_filter_only = "risk_filter_only"
    blocked = "blocked"
    needs_review = "needs_review"
    deprecated = "deprecated"


class FeaturePromotionSeverity:
    info = "info"
    warning = "warning"
    error = "error"
    blocker = "blocker"


@dataclass(frozen=True)
class FeaturePromotionPolicy:
    policy_id: str
    policy_name: str
    feature_set_name: str
    feature_set_hash: str
    default_weak_pit_action: str = FeaturePromotionStatus.needs_review
    default_unsafe_action: str = FeaturePromotionStatus.blocked
    require_availability_field: bool = True
    require_leakage_audit: bool = False
    require_coverage_min: float = 0.0
    require_manual_approval_for_weak_pit: bool = True
    allowed_feature_families: list[str] = field(default_factory=list)
    denied_features: list[str] = field(default_factory=list)
    family_rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeaturePromotionCandidate:
    feature_name: str
    feature_family: str
    feature_set_name: str
    feature_set_hash: str
    required_datasets: list[str]
    optional_datasets: list[str]
    source_fields: list[str]
    date_field: str
    availability_field: str | None
    pit_safety: str
    current_default_enabled: bool
    proposed_status: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeaturePromotionEvidence:
    feature_name: str
    pit_contract_status: str
    availability_field_status: str
    coverage_status: str
    leakage_audit_status: str
    sample_alignment_status: str
    feature_tensor_coverage: float
    weak_pit_reason: str
    artifact_refs: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeaturePromotionDecision:
    feature_name: str
    decision: str
    status: str
    approved_for_alpha: bool
    approved_for_risk_filter: bool
    blocked_reason: str | None = None
    reviewer: str | None = None
    approval_id: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeaturePromotionReviewPackage:
    review_id: str
    policy: dict[str, Any]
    summary: dict[str, Any]
    candidates: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
