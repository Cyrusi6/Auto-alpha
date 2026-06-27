"""Local paper broker for A-share orders."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from backtest import AShareCostModel, AShareTradingRules

from .exporter import export_fills_jsonl
from .models import ExecutionFill, ExecutionOrder


class PaperBroker:
    def __init__(
        self,
        output_dir: str | Path,
        cost_model: AShareCostModel | None = None,
        trading_rules: AShareTradingRules | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.cost_model = cost_model or AShareCostModel()
        self.trading_rules = trading_rules or AShareTradingRules()

    def submit_orders(
        self,
        orders: Sequence[ExecutionOrder],
        prices: dict[str, float],
        trade_date: str,
    ) -> list[ExecutionFill]:
        fills: list[ExecutionFill] = []
        for order in orders:
            price = float(prices.get(order.ts_code, 0.0))
            if price <= 0:
                fills.append(self._rejected(order, trade_date, price, "missing_price"))
                continue
            shares = self.trading_rules.round_shares(float(order.order_value) / price)
            if shares <= 0:
                fills.append(self._rejected(order, trade_date, price, "zero_shares"))
                continue
            value = shares * price
            fills.append(
                ExecutionFill(
                    trade_date=trade_date,
                    ts_code=order.ts_code,
                    side=order.side.upper(),
                    price=price,
                    shares=int(shares),
                    value=float(value),
                    status="FILLED",
                )
            )
        export_fills_jsonl(fills, self.output_dir / "paper_fills.jsonl")
        return fills

    @staticmethod
    def _rejected(order: ExecutionOrder, trade_date: str, price: float, reason: str) -> ExecutionFill:
        return ExecutionFill(
            trade_date=trade_date,
            ts_code=order.ts_code,
            side=order.side.upper(),
            price=float(price),
            shares=0,
            value=0.0,
            status="REJECTED",
            reason=reason,
        )
