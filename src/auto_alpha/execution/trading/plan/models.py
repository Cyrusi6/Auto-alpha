"""Execution plan dataclasses for local paper order slicing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParentOrder:
    parent_order_id: str
    trade_date: str
    ts_code: str
    side: str
    target_weight: float
    order_value: float
    reason: str = "rebalance"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChildOrder:
    child_order_id: str
    parent_order_id: str
    trade_date: str
    ts_code: str
    side: str
    bucket: str
    order_value: float
    target_weight: float = 0.0
    reason: str = "rebalance"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionSchedule:
    trade_date: str
    parent_orders: list[ParentOrder]
    child_orders: list[ChildOrder]
    buckets: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "parent_orders": [order.to_dict() for order in self.parent_orders],
            "child_orders": [order.to_dict() for order in self.child_orders],
            "buckets": list(self.buckets),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ExecutionQualitySummary:
    parent_order_count: int
    child_order_count: int
    filled_child_orders: int
    partial_child_orders: int
    rejected_child_orders: int
    requested_value: float
    filled_value: float
    unfilled_order_value: float
    estimated_impact_cost: float
    realized_execution_cost: float
    execution_fill_rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionPlanResult:
    schedule: ExecutionSchedule
    fills: list[object]
    quality: ExecutionQualitySummary
    capacity_report: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule": self.schedule.to_dict(),
            "fills": [_payload(fill) for fill in self.fills],
            "quality": self.quality.to_dict(),
            "capacity_report": self.capacity_report,
        }


def _payload(fill: object) -> dict[str, Any]:
    if hasattr(fill, "__dataclass_fields__"):
        return {field: getattr(fill, field) for field in fill.__dataclass_fields__}
    return dict(fill)
