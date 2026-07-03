"""Dataclasses for portfolio certification campaigns."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PortfolioCertificationCampaignRecord:
    portfolio_campaign_id: str
    source_factor_certification_campaign_id: str | None
    certified_factor_pool_path: str
    data_freeze_id: str | None = None
    portfolio_policy_profile: str = "sample_lenient_portfolio"
    scenario_profile: str = "sample"
    factor_count: int = 0
    status: str = "registered"
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioCandidateItemRecord:
    item_id: str
    factor_id: str
    formula_hash: str = ""
    certified_factor_pool_rank: int = 0
    factor_store_dir: str = ""
    portfolio_lab_output_dir: str = ""
    portfolio_lab_report_path: str | None = None
    selected_portfolio_policy_path: str | None = None
    portfolio_certification_decision_path: str | None = None
    certified_portfolio_policy_path: str | None = None
    status: str = "pending"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionCandidateBundleRecord:
    production_candidate_bundle_id: str
    factor_id: str
    model_version_id: str | None
    portfolio_policy_id: str | None
    optimizer_policy_model_version_id: str | None
    factor_certification_status: str
    portfolio_certification_status: str
    validation_score: float = 0.0
    portfolio_score: float = 0.0
    scenario_pass_ratio: float = 0.0
    capacity_summary: dict[str, Any] = field(default_factory=dict)
    risk_summary: dict[str, Any] = field(default_factory=dict)
    settlement_summary: dict[str, Any] = field(default_factory=dict)
    readiness_status: str = "pending_activation_review"
    selected_for_activation_review: bool = True
    reason: str = ""
    artifact_refs: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
