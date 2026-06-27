"""Dataclasses for A-share portfolio simulation."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TargetPosition:
    trade_date: str
    ts_code: str
    target_weight: float
    factor_value: float | None = None


@dataclass(frozen=True)
class TradeOrder:
    trade_date: str
    ts_code: str
    side: str
    target_weight: float
    current_weight: float
    order_value: float
    reason: str = "rebalance"


@dataclass(frozen=True)
class TradeFill:
    trade_date: str
    ts_code: str
    side: str
    price: float
    shares: int
    value: float
    cost: float
    allowed: bool = True
    reason: str = ""


@dataclass(frozen=True)
class PortfolioSnapshot:
    trade_date: str
    equity: float
    cash: float
    positions_value: float
    daily_return: float
    turnover: float
    cost: float
    n_positions: int


@dataclass(frozen=True)
class PortfolioBacktestResult:
    snapshots: list[PortfolioSnapshot]
    fills: list[TradeFill]
    metrics: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshots": [asdict(snapshot) for snapshot in self.snapshots],
            "fills": [asdict(fill) for fill in self.fills],
            "metrics": {key: float(value) for key, value in self.metrics.items()},
        }
