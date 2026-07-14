"""Dataclasses for versioned A-share feature sets."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class FeatureFamily:
    price_return = "price_return"
    price_volume_core = "price_volume_core"
    index_market = "index_market"
    liquidity = "liquidity"
    volatility = "volatility"
    valuation = "valuation"
    quality = "quality"
    growth = "growth"
    size = "size"
    risk = "risk"
    index_membership = "index_membership"
    corporate_action = "corporate_action"
    limit_suspension = "limit_suspension"
    industry = "industry"
    suspension_status = "suspension_status"
    financial_statement = "financial_statement"
    earnings_event = "earnings_event"
    moneyflow = "moneyflow"
    margin = "margin"
    abnormal_trading = "abnormal_trading"
    holder_structure = "holder_structure"
    pledge_repurchase_unlock = "pledge_repurchase_unlock"
    northbound = "northbound"


@dataclass(frozen=True)
class FeatureDefinition:
    feature_name: str
    feature_version: str
    family: str
    source_fields: list[str]
    tensor_key: str
    feature_set_name: str = ""
    required_datasets: list[str] = field(default_factory=list)
    optional_datasets: list[str] = field(default_factory=list)
    date_field: str = "trade_date"
    availability_field: str | None = None
    pit_safety: str = "pit_safe"
    transform: str = "robust_zscore"
    lookback: int = 1
    point_in_time_safe: bool = True
    availability_contract: dict[str, Any] = field(default_factory=dict)
    default_enabled: bool = True
    used_for_alpha: bool = True
    used_for_filter: bool = False
    used_for_risk: bool = False
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    dependency_graph: dict[str, Any] = field(default_factory=dict)
    effective_lookback: int = 1
    price_basis: str = "not_applicable"
    pit_availability: str = "same_trade_date"
    validity_rule: str = "all_sources_valid_for_required_history"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureSetManifest:
    feature_set_name: str
    feature_set_version: str
    feature_version: str
    operator_version: str
    feature_count: int
    feature_definitions: list[dict[str, Any]]
    data_freeze_id: str | None
    data_freeze_hash: str | None
    point_in_time: bool
    corporate_action_aware: bool
    target_return_mode: str
    created_at: str
    content_hash: str
    feature_promotion_policy_hash: str | None = None
    feature_promotion_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureCoverageReport:
    feature_set_name: str
    feature_set_version: str
    feature_count: int
    rows: int
    cols: int
    warnings: list[str]
    feature_summaries: list[dict[str, Any]]
    created_at: str
    raw_data_index_used: bool = False
    dataset_index_status: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureTensorBuildResult:
    feature_set_name: str
    feature_set_version: str
    feature_count: int
    n_stocks: int
    n_dates: int
    tensor_path: str
    manifest_path: str
    coverage_report_path: str
    values_summary_path: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
