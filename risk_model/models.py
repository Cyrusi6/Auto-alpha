"""Dataclasses for A-share portfolio risk analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SecurityExposure:
    ts_code: str
    industry: str
    log_mkt_cap: float
    volatility: float
    beta: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioExposure:
    industry_weights: dict[str, float]
    size_exposure: float
    volatility_exposure: float
    beta_exposure: float
    concentration_hhi: float
    top_weight: float
    n_positions: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkExposure:
    index_code: str
    as_of_date: str
    weights: dict[str, float]
    exposure: PortfolioExposure

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskConstraintConfig:
    max_weight: float = 0.10
    max_industry_active_weight: float = 0.20
    max_total_active_weight: float = 1.00
    max_tracking_error: float = 1.00
    max_turnover: float = 1.00
    min_names: int = 1
    max_names: int = 100
    max_hhi: float = 1.00


@dataclass(frozen=True)
class RiskMetrics:
    portfolio_volatility: float
    tracking_error: float
    active_share: float
    hhi: float
    top_weight: float
    n_positions: float
    industry_active_max: float
    total_active_weight: float
    turnover: float = 0.0
    violations: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {key: float(value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class RiskReport:
    factor_id: str | None
    index_code: str
    as_of_date: str
    portfolio: PortfolioExposure
    benchmark: BenchmarkExposure
    active: PortfolioExposure
    metrics: RiskMetrics
    violations: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    style_exposures: dict[str, float] | None = None
    active_style_exposures: dict[str, float] | None = None
    industry_exposures: dict[str, float] | None = None
    factor_covariance_summary: dict[str, float] | None = None
    specific_risk_summary: dict[str, float] | None = None
    factor_risk_contribution: dict[str, Any] | None = None
    active_risk_contribution: dict[str, Any] | None = None
    attribution_summary: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "index_code": self.index_code,
            "as_of_date": self.as_of_date,
            "portfolio": self.portfolio.to_dict(),
            "benchmark": self.benchmark.to_dict(),
            "active": self.active.to_dict(),
            "metrics": self.metrics.to_dict(),
            "violations": list(self.violations),
            "checks": self.checks,
            "style_exposures": self.style_exposures or {},
            "active_style_exposures": self.active_style_exposures or {},
            "industry_exposures": self.industry_exposures or {},
            "factor_covariance_summary": self.factor_covariance_summary or {},
            "specific_risk_summary": self.specific_risk_summary or {},
            "factor_risk_contribution": self.factor_risk_contribution or {},
            "active_risk_contribution": self.active_risk_contribution or {},
            "attribution_summary": self.attribution_summary or {},
        }


@dataclass(frozen=True)
class FactorModelSpec:
    style_factors: list[str]
    industry_factors: list[str]
    shrinkage: float = 0.1
    lookback: int | None = None

    @property
    def factor_names(self) -> list[str]:
        return [*self.style_factors, *self.industry_factors]


@dataclass(frozen=True)
class FactorExposureMatrix:
    factor_names: list[str]
    style_factor_names: list[str]
    industry_factor_names: list[str]
    exposures: Any


@dataclass(frozen=True)
class FactorReturnSeries:
    factor_names: list[str]
    trade_dates: list[str]
    returns: Any


@dataclass(frozen=True)
class FactorRiskModel:
    spec: FactorModelSpec
    exposure_matrix: FactorExposureMatrix
    factor_returns: FactorReturnSeries
    factor_covariance: Any
    specific_risk: Any
    ts_codes: list[str]
    trade_dates: list[str]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": asdict(self.spec),
            "factor_names": list(self.exposure_matrix.factor_names),
            "style_factor_names": list(self.exposure_matrix.style_factor_names),
            "industry_factor_names": list(self.exposure_matrix.industry_factor_names),
            "ts_codes": list(self.ts_codes),
            "trade_dates": list(self.trade_dates),
            "summary": self.summary,
        }
