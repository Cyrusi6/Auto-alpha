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
    broker_order_id: str | None = None
    broker_fill_id: str | None = None
    client_order_id: str | None = None
    broker_adapter: str | None = None
    broker_batch_id: str | None = None
    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0
    slippage: float = 0.0
    market_impact: float = 0.0
    other_fee: float = 0.0
    cost_breakdown: dict[str, float] | None = None
