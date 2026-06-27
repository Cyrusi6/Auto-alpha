"""A-share strategy risk checks."""

from __future__ import annotations

from backtest import TargetPosition
from execution import ExecutionOrder


class AShareRiskEngine:
    def __init__(self, max_weight: float = 0.10, max_names: int = 50, min_order_value: float = 0.0):
        self.max_weight = float(max_weight)
        self.max_names = int(max_names)
        self.min_order_value = float(min_order_value)

    def validate_targets(self, targets: list[TargetPosition]) -> tuple[bool, list[str]]:
        errors: list[str] = []
        if len(targets) > self.max_names:
            errors.append(f"target count exceeds max_names: {len(targets)} > {self.max_names}")
        for target in targets:
            if target.target_weight < 0:
                errors.append(f"{target.ts_code} has negative target weight")
            if target.target_weight > self.max_weight:
                errors.append(f"{target.ts_code} exceeds max_weight")
        return not errors, errors

    def filter_orders(self, orders: list[ExecutionOrder]) -> list[ExecutionOrder]:
        return [order for order in orders if float(order.order_value) >= self.min_order_value]
