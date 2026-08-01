"""Dataclasses for local A-share capacity analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SecurityCapacity:
    ts_code: str
    trade_date: str
    side: str
    order_value: float
    order_shares: int
    avg_daily_amount: float
    avg_daily_volume: float
    volatility: float
    amount_participation: float
    volume_participation: float
    max_trade_value: float
    max_trade_shares: int
    estimated_impact_cost: float
    capacity_score: float
    capacity_warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioCapacity:
    trade_date: str
    records: list[SecurityCapacity]
    total_order_value: float
    max_amount_participation: float
    max_volume_participation: float
    estimated_impact_cost: float
    capacity_warning_count: int
    capacity_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "records": [record.to_dict() for record in self.records],
            "total_order_value": float(self.total_order_value),
            "max_amount_participation": float(self.max_amount_participation),
            "max_volume_participation": float(self.max_volume_participation),
            "estimated_impact_cost": float(self.estimated_impact_cost),
            "capacity_warning_count": int(self.capacity_warning_count),
            "capacity_score": float(self.capacity_score),
        }


@dataclass(frozen=True)
class CapacityConfig:
    lookback: int = 20
    max_participation: float = 0.10
    impact_base_bps: float = 5.0
    impact_power: float = 0.5


@dataclass(frozen=True)
class CapacityReport:
    trade_date: str
    config: CapacityConfig
    portfolio: PortfolioCapacity
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "config": asdict(self.config),
            "portfolio": self.portfolio.to_dict(),
            "created_at": self.created_at,
            "metadata": self.metadata,
        }
