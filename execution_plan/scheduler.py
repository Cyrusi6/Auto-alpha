"""Order scheduling and slicing utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from capacity_model import CapacityConfig, estimate_portfolio_capacity

from .models import ChildOrder, ExecutionSchedule, ParentOrder


DEFAULT_BUCKETS = ("open", "morning", "afternoon", "close")


@dataclass(frozen=True)
class ExecutionPlanConfig:
    buckets: tuple[str, ...] = DEFAULT_BUCKETS
    max_child_participation: float = 0.10
    min_child_order_value: float = 0.0
    lot_size: int = 100
    allow_partial: bool = True
    price_field: str = "close"
    capacity_lookback: int = 20
    impact_base_bps: float = 5.0
    impact_power: float = 0.5


def build_parent_orders_from_target_orders(target_orders: Sequence[object]) -> list[ParentOrder]:
    parents: list[ParentOrder] = []
    for idx, order in enumerate(target_orders):
        payload = _payload(order)
        trade_date = str(payload.get("trade_date"))
        ts_code = str(payload.get("ts_code"))
        side = str(payload.get("side", "BUY")).upper()
        parent_id = str(payload.get("parent_order_id") or f"parent_{trade_date}_{ts_code}_{idx:04d}")
        parents.append(
            ParentOrder(
                parent_order_id=parent_id,
                trade_date=trade_date,
                ts_code=ts_code,
                side=side,
                target_weight=float(payload.get("target_weight", 0.0) or 0.0),
                order_value=float(payload.get("order_value", 0.0) or 0.0),
                reason=str(payload.get("reason") or "rebalance"),
            )
        )
    return parents


def slice_parent_order(parent: ParentOrder, capacity, buckets: Sequence[str], config: ExecutionPlanConfig | None = None) -> list[ChildOrder]:
    config = config or ExecutionPlanConfig(buckets=tuple(buckets))
    bucket_list = tuple(buckets) or DEFAULT_BUCKETS
    max_value = max(float(capacity.max_trade_value), 0.0)
    remaining = max(float(parent.order_value), 0.0)
    if remaining <= 0:
        return []
    base_slice = remaining / len(bucket_list)
    if max_value > 0:
        base_slice = min(base_slice, max_value / len(bucket_list))
    child_orders: list[ChildOrder] = []
    for bucket_idx, bucket in enumerate(bucket_list):
        if remaining <= 1e-9:
            break
        value = min(base_slice, remaining)
        if value < config.min_child_order_value and remaining > config.min_child_order_value:
            continue
        child_orders.append(
            ChildOrder(
                child_order_id=f"child_{parent.parent_order_id}_{bucket_idx:02d}",
                parent_order_id=parent.parent_order_id,
                trade_date=parent.trade_date,
                ts_code=parent.ts_code,
                side=parent.side,
                bucket=str(bucket),
                order_value=float(value),
                target_weight=parent.target_weight,
                reason=parent.reason,
            )
        )
        remaining -= value
    return child_orders


def build_execution_schedule(
    parent_orders: Sequence[ParentOrder],
    loader,
    as_of_date: str,
    config: ExecutionPlanConfig | None = None,
) -> tuple[ExecutionSchedule, object]:
    config = config or ExecutionPlanConfig()
    capacity_config = CapacityConfig(
        lookback=config.capacity_lookback,
        max_participation=config.max_child_participation,
        impact_base_bps=config.impact_base_bps,
        impact_power=config.impact_power,
    )
    portfolio_capacity = estimate_portfolio_capacity(loader, parent_orders, as_of_date, capacity_config)
    capacity_by_code = {record.ts_code: record for record in portfolio_capacity.records}
    child_orders: list[ChildOrder] = []
    for parent in parent_orders:
        child_orders.extend(slice_parent_order(parent, capacity_by_code[parent.ts_code], config.buckets, config))
    schedule = ExecutionSchedule(
        trade_date=as_of_date,
        parent_orders=list(parent_orders),
        child_orders=child_orders,
        buckets=list(config.buckets),
        metadata={
            "max_child_participation": config.max_child_participation,
            "min_child_order_value": config.min_child_order_value,
            "price_field": config.price_field,
            "estimated_impact_cost": portfolio_capacity.estimated_impact_cost,
            "capacity_warning_count": portfolio_capacity.capacity_warning_count,
        },
    )
    return schedule, portfolio_capacity


def _payload(order: object) -> dict[str, object]:
    if hasattr(order, "__dataclass_fields__"):
        return {field: getattr(order, field) for field in order.__dataclass_fields__}
    return dict(order)
