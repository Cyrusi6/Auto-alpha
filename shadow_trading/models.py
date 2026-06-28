"""Shadow trading dataclasses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ShadowRunStatus:
    planned = "planned"
    running = "running"
    success = "success"
    warning = "warning"
    failed = "failed"


class ShadowExecutionMode:
    no_broker = "no_broker"
    simulated_fills = "simulated_fills"
    compare_only = "compare_only"


@dataclass(frozen=True)
class ShadowOrder:
    shadow_order_id: str
    production_run_id: str
    trade_date: str
    ts_code: str
    side: str
    order_value: float
    target_weight: float = 0.0
    parent_order_id: str | None = None
    child_order_id: str | None = None
    bucket: str | None = None
    reason: str = "shadow"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowFill:
    shadow_fill_id: str
    shadow_order_id: str
    production_run_id: str
    trade_date: str
    ts_code: str
    side: str
    value: float
    status: str
    price: float = 0.0
    shares: int = 0
    cost: float = 0.0
    reason: str = ""
    parent_order_id: str | None = None
    child_order_id: str | None = None
    bucket: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowPosition:
    production_run_id: str
    trade_date: str
    ts_code: str
    shares: int
    market_value: float
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowAccountSnapshot:
    production_run_id: str
    trade_date: str
    cash: float
    equity: float
    position_value: float
    turnover: float
    fill_rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowDriftRecord:
    production_run_id: str
    trade_date: str
    metric: str
    value: float
    threshold: float | None = None
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowPerformanceReport:
    production_run_id: str
    trade_date: str
    metrics: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowRunReport:
    production_run_id: str
    trade_date: str
    as_of_date: str
    status: str
    execution_mode: str
    summary: dict[str, Any]
    orders: list[ShadowOrder] = field(default_factory=list)
    fills: list[ShadowFill] = field(default_factory=list)
    positions: list[ShadowPosition] = field(default_factory=list)
    snapshots: list[ShadowAccountSnapshot] = field(default_factory=list)
    drift: list[ShadowDriftRecord] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "production_run_id": self.production_run_id,
            "trade_date": self.trade_date,
            "as_of_date": self.as_of_date,
            "status": self.status,
            "execution_mode": self.execution_mode,
            "summary": dict(self.summary),
            "orders": [item.to_dict() for item in self.orders],
            "fills": [item.to_dict() for item in self.fills],
            "positions": [item.to_dict() for item in self.positions],
            "snapshots": [item.to_dict() for item in self.snapshots],
            "drift": [item.to_dict() for item in self.drift],
            "paths": dict(self.paths),
        }
