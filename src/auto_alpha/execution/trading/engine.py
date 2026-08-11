"""Execution plan export and paper-broker boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AShareExecutionConfig:
    output_dir: Path
    default_price_field: str = "close"
    paper_account_id: str = "paper_ashare"
    allow_live_trading: bool = False

    @classmethod
    def from_env(cls) -> "AShareExecutionConfig":
        return cls(output_dir=Path(os.getenv("ASHARE_EXECUTION_OUTPUT_DIR", "artifacts/execution")))

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

import csv
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Sequence



def _record_payload(record: object) -> dict[str, object]:
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)
    if isinstance(record, dict):
        return dict(record)
    raise TypeError(f"unsupported export record: {type(record)!r}")


def export_orders_csv(orders: Sequence[ExecutionOrder], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payloads = [_record_payload(order) for order in orders]
    fieldnames = sorted({key for payload in payloads for key in payload}) if payloads else []
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(payloads)
    return output_path


def export_orders_jsonl(orders: Sequence[ExecutionOrder], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for order in orders:
            handle.write(json.dumps(_record_payload(order), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return output_path


def export_fills_jsonl(fills: Sequence[ExecutionFill], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for fill in fills:
            handle.write(json.dumps(_record_payload(fill), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return output_path

from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from auto_alpha.portfolio.simulator.backtest import AShareCostModel
from auto_alpha.portfolio.simulator.backtest import AShareTradingRules



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

__all__ = [
    "AShareExecutionConfig",
    "ExecutionFill",
    "ExecutionOrder",
    "PaperBroker",
    "export_fills_jsonl",
    "export_orders_csv",
    "export_orders_jsonl",
]
