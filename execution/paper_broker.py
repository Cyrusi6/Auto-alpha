"""Local paper broker for A-share orders."""

from __future__ import annotations

from dataclasses import asdict
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
        account=None,
    ):
        self.output_dir = Path(output_dir)
        self.cost_model = cost_model or AShareCostModel()
        self.trading_rules = trading_rules or AShareTradingRules()
        self.account = account

    def submit_orders(
        self,
        orders: Sequence[ExecutionOrder],
        prices: dict[str, float],
        trade_date: str,
        volumes: dict[str, float] | None = None,
        suspended: dict[str, bool] | None = None,
        limit_up: dict[str, bool] | None = None,
        limit_down: dict[str, bool] | None = None,
    ) -> list[ExecutionFill]:
        volumes = volumes or {}
        suspended = suspended or {}
        limit_up = limit_up or {}
        limit_down = limit_down or {}
        fills: list[ExecutionFill] = []
        for order in orders:
            side = order.side.upper()
            price = float(prices.get(order.ts_code, 0.0))
            if price <= 0:
                fills.append(self._rejected(order, trade_date, price, "missing_price"))
                continue
            if side == "BUY":
                allowed, reason = self.trading_rules.can_buy(
                    price,
                    is_suspended=bool(suspended.get(order.ts_code, False)),
                    is_limit_up=bool(limit_up.get(order.ts_code, False)),
                )
            else:
                allowed, reason = self.trading_rules.can_sell(
                    price,
                    is_suspended=bool(suspended.get(order.ts_code, False)),
                    is_limit_down=bool(limit_down.get(order.ts_code, False)),
                )
            if not allowed:
                fills.append(self._rejected(order, trade_date, price, reason))
                continue
            requested_shares = self.trading_rules.round_shares(float(order.order_value) / price)
            if order.ts_code in volumes:
                shares, volume_reason = self.trading_rules.volume_limited_shares(
                    requested_shares,
                    float(volumes.get(order.ts_code, 0.0)),
                )
            else:
                shares, volume_reason = requested_shares, ""
            status = "FILLED"
            if shares <= 0:
                fills.append(self._rejected(order, trade_date, price, volume_reason or "zero_shares"))
                continue
            if shares < requested_shares:
                status = "PARTIAL"
            value = shares * price
            breakdown = self.cost_model.estimate(side, value)
            cost = breakdown.total
            fills.append(
                ExecutionFill(
                    trade_date=trade_date,
                    ts_code=order.ts_code,
                    side=side,
                    price=price,
                    shares=int(shares),
                    value=float(value),
                    status=status,
                    cost=float(cost),
                    reason=volume_reason if status == "PARTIAL" else "",
                    commission=float(breakdown.commission),
                    stamp_duty=float(breakdown.stamp_duty),
                    transfer_fee=float(breakdown.transfer_fee),
                    slippage=float(breakdown.slippage),
                    market_impact=float(breakdown.market_impact),
                    cost_breakdown=asdict(breakdown),
                )
            )
        export_fills_jsonl(fills, self.output_dir / "paper_fills.jsonl")
        if self.account is not None:
            self.account.apply_fills(fills, prices, trade_date)
            self.account.mark_to_market(prices, trade_date)
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
            cost=0.0,
            reason=reason,
        )
