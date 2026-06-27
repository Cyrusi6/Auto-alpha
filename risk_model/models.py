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
        }
