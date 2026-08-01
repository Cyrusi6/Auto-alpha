"""A-share strategy target book."""

from __future__ import annotations

from dataclasses import dataclass

from backtest import TargetPosition
from execution import ExecutionOrder


@dataclass(frozen=True)
class StrategyTargetBook:
    trade_date: str
    targets: list[TargetPosition]

    def to_orders(
        self,
        current_weights: dict[str, float] | None = None,
        portfolio_value: float = 1_000_000.0,
    ) -> list[ExecutionOrder]:
        current_weights = current_weights or {}
        orders: list[ExecutionOrder] = []
        target_codes = {target.ts_code for target in self.targets}
        for target in self.targets:
            current_weight = float(current_weights.get(target.ts_code, 0.0))
            delta = float(target.target_weight) - current_weight
            if abs(delta) <= 1e-12:
                continue
            orders.append(
                ExecutionOrder(
                    trade_date=self.trade_date,
                    ts_code=target.ts_code,
                    side="BUY" if delta > 0 else "SELL",
                    target_weight=float(target.target_weight),
                    order_value=abs(delta) * portfolio_value,
                )
            )
        for ts_code, current_weight in current_weights.items():
            if ts_code in target_codes or current_weight <= 0:
                continue
            orders.append(
                ExecutionOrder(
                    trade_date=self.trade_date,
                    ts_code=ts_code,
                    side="SELL",
                    target_weight=0.0,
                    order_value=current_weight * portfolio_value,
                )
            )
        return orders
