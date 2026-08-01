"""Dataclasses for factor certification campaign storage."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FactorCertificationCampaignRecord:
    certification_campaign_id: str
    source_validation_campaign_id: str | None
    source_certification_queue_path: str
    data_freeze_id: str | None = None
    feature_set_name: str | None = None
    certification_policy_profile: str = "sample_lenient_certification"
    candidate_count: int = 0
    status: str = "registered"
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactorCertificationItemRecord:
    item_id: str
    queue_id: str
    factor_id: str
    formula_hash: str = ""
    validation_rank: int = 0
    validation_score: float = 0.0
    certification_policy_profile: str = "sample_lenient_certification"
    factor_store_dir: str = ""
    status: str = "pending"
    output_dir: str = ""
    decision_path: str | None = None
    scorecard_path: str | None = None
    package_path: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CertifiedFactorPoolRecord:
    certified_factor_pool_id: str
    factor_id: str
    formula_hash: str
    certification_status: str
    validation_score: float = 0.0
    certification_score: float = 0.0
    priority: int = 0
    factor_store_dir: str = ""
    validation_artifacts: dict[str, str] = field(default_factory=dict)
    certification_artifacts: dict[str, str] = field(default_factory=dict)
    selected_for_portfolio_lab: bool = True
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
