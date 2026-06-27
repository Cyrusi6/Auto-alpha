"""Impact cost helpers for local capacity analysis."""

from __future__ import annotations

import math
from dataclasses import replace

from .models import SecurityCapacity


def estimate_impact_cost(
    order_value: float,
    avg_daily_amount: float,
    volatility: float,
    side: str,
    base_bps: float = 5.0,
    impact_power: float = 0.5,
) -> float:
    value = max(float(order_value), 0.0)
    amount = max(float(avg_daily_amount), 1.0)
    vol = max(float(volatility), 0.0)
    if value <= 0 or not math.isfinite(value):
        return 0.0
    participation = max(value / amount, 0.0)
    side_multiplier = 1.0 if str(side).upper() == "BUY" else 0.9
    impact_bps = float(base_bps) * (participation ** max(float(impact_power), 0.01)) * (1.0 + vol) * side_multiplier
    return float(value * impact_bps / 10000.0)


def estimate_capacity_adjusted_order(order, capacity: SecurityCapacity):
    order_value = min(float(getattr(order, "order_value", 0.0)), float(capacity.max_trade_value))
    if order_value == float(getattr(order, "order_value", 0.0)):
        return order
    if hasattr(order, "__dataclass_fields__"):
        return replace(order, order_value=float(order_value), reason=f"{getattr(order, 'reason', 'rebalance')}:capacity_adjusted")
    payload = dict(order)
    payload["order_value"] = float(order_value)
    payload["reason"] = f"{payload.get('reason', 'rebalance')}:capacity_adjusted"
    return payload
