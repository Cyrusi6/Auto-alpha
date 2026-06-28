"""Dataclasses for versioned A-share feature sets."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class FeatureFamily:
    price_return = "price_return"
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


@dataclass(frozen=True)
class FeatureDefinition:
    feature_name: str
    feature_version: str
    family: str
    source_fields: list[str]
    tensor_key: str
    transform: str = "robust_zscore"
    lookback: int = 1
    point_in_time_safe: bool = True
    availability_contract: dict[str, Any] = field(default_factory=dict)
    default_enabled: bool = True
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

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
