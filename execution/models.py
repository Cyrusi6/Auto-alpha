"""Paper execution records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionOrder:
    trade_date: str
    ts_code: str
    side: str
    target_weight: float
    order_value: float
    reason: str = "rebalance"


@dataclass(frozen=True)
class ExecutionFill:
    trade_date: str
    ts_code: str
    side: str
    price: float
    shares: int
    value: float
    status: str
    cost: float = 0.0
    reason: str = ""
    parent_order_id: str | None = None
    child_order_id: str | None = None
    bucket: str | None = None
