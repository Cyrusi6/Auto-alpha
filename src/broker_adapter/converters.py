"""Converters between execution plans, broker requests, and paper fills."""

from __future__ import annotations

import hashlib
from typing import Any, Sequence

from backtest import AShareTradingRules
from execution import ExecutionFill
from execution_plan import ChildOrder

from .models import BrokerFillRecord, BrokerOrderRequest


def build_broker_requests_from_child_orders(
    child_orders: Sequence[ChildOrder | dict[str, Any]],
    prices: dict[str, float],
    trade_date: str,
    batch_id: str,
    trading_rules: AShareTradingRules | None = None,
    price_type: str = "MARKET",
) -> list[BrokerOrderRequest]:
    trading_rules = trading_rules or AShareTradingRules()
    requests: list[BrokerOrderRequest] = []
    for raw in child_orders:
        order = _child_payload(raw)
        ts_code = str(order.get("ts_code") or "")
        price = float(prices.get(ts_code, 0.0) or 0.0)
        order_value = float(order.get("order_value") or 0.0)
        shares = trading_rules.round_shares(order_value / price) if price > 0 else 0
        child_order_id = order.get("child_order_id")
        client_order_id = str(child_order_id or _client_order_id(batch_id, order))
        requests.append(
            BrokerOrderRequest(
                client_order_id=client_order_id,
                batch_id=batch_id,
                trade_date=trade_date,
                ts_code=ts_code,
                side=str(order.get("side") or "").upper(),
                shares=int(shares),
                order_value=order_value,
                price=price,
                price_type=price_type,
                parent_order_id=order.get("parent_order_id"),
                child_order_id=child_order_id,
                bucket=order.get("bucket"),
                metadata={"source": "child_order", "reason": order.get("reason", "")},
            )
        )
    return requests


def broker_fills_to_execution_fills(fills: Sequence[BrokerFillRecord | dict[str, Any]]) -> list[ExecutionFill]:
    records: list[ExecutionFill] = []
    for fill in fills:
        payload = fill.to_dict() if hasattr(fill, "to_dict") else dict(fill)
        records.append(
            ExecutionFill(
                trade_date=str(payload.get("trade_date") or ""),
                ts_code=str(payload.get("ts_code") or ""),
                side=str(payload.get("side") or "").upper(),
                price=float(payload.get("price") or 0.0),
                shares=int(payload.get("shares") or 0),
                value=float(payload.get("value") or 0.0),
                status=str(payload.get("status") or ""),
                cost=float(payload.get("cost") or 0.0),
                reason=str(payload.get("reason") or ""),
                parent_order_id=payload.get("parent_order_id"),
                child_order_id=payload.get("child_order_id"),
                bucket=payload.get("bucket"),
                broker_order_id=payload.get("broker_order_id"),
                broker_fill_id=payload.get("broker_fill_id"),
                client_order_id=payload.get("client_order_id"),
                broker_adapter=payload.get("broker_adapter"),
                broker_batch_id=payload.get("batch_id"),
                commission=float(payload.get("commission") or 0.0),
                stamp_duty=float(payload.get("stamp_duty") or 0.0),
                transfer_fee=float(payload.get("transfer_fee") or 0.0),
                slippage=float(payload.get("slippage") or 0.0),
                market_impact=float(payload.get("market_impact") or 0.0),
                other_fee=float(payload.get("other_fee") or 0.0),
                cost_breakdown=dict(payload.get("cost_breakdown") or {}),
            )
        )
    return records


def execution_fills_to_broker_fills(
    fills: Sequence[ExecutionFill | dict[str, Any]],
    batch_id: str,
    adapter_name: str = "paper",
) -> list[BrokerFillRecord]:
    records: list[BrokerFillRecord] = []
    for fill in fills:
        payload = fill.__dict__ if hasattr(fill, "__dataclass_fields__") else dict(fill)
        broker_order_id = str(payload.get("broker_order_id") or payload.get("child_order_id") or _fill_hash(payload, batch_id))
        broker_fill_id = str(payload.get("broker_fill_id") or f"bf_{_fill_hash(payload, batch_id)}")
        records.append(
            BrokerFillRecord(
                broker_fill_id=broker_fill_id,
                broker_order_id=broker_order_id,
                client_order_id=str(payload.get("client_order_id") or payload.get("child_order_id") or broker_order_id),
                batch_id=batch_id,
                trade_date=str(payload.get("trade_date") or ""),
                ts_code=str(payload.get("ts_code") or ""),
                side=str(payload.get("side") or ""),
                price=float(payload.get("price") or 0.0),
                shares=int(payload.get("shares") or 0),
                value=float(payload.get("value") or 0.0),
                cost=float(payload.get("cost") or 0.0),
                status=str(payload.get("status") or ""),
                reason=str(payload.get("reason") or ""),
                commission=float(payload.get("commission") or 0.0),
                stamp_duty=float(payload.get("stamp_duty") or 0.0),
                transfer_fee=float(payload.get("transfer_fee") or 0.0),
                slippage=float(payload.get("slippage") or 0.0),
                market_impact=float(payload.get("market_impact") or 0.0),
                other_fee=float(payload.get("other_fee") or 0.0),
                cost_breakdown=dict(payload.get("cost_breakdown") or {}),
                parent_order_id=payload.get("parent_order_id"),
                child_order_id=payload.get("child_order_id"),
                bucket=payload.get("bucket"),
                broker_adapter=adapter_name,
            )
        )
    return records


def _child_payload(order: ChildOrder | dict[str, Any]) -> dict[str, Any]:
    if hasattr(order, "to_dict"):
        return order.to_dict()
    return dict(order)


def _client_order_id(batch_id: str, payload: dict[str, Any]) -> str:
    base = "|".join(
        [
            batch_id,
            str(payload.get("ts_code") or ""),
            str(payload.get("side") or ""),
            str(payload.get("bucket") or ""),
            str(payload.get("order_value") or ""),
        ]
    )
    return "co_" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:20]


def _fill_hash(payload: dict[str, Any], batch_id: str) -> str:
    base = "|".join(
        [
            batch_id,
            str(payload.get("trade_date") or ""),
            str(payload.get("child_order_id") or ""),
            str(payload.get("ts_code") or ""),
            str(payload.get("side") or ""),
            str(payload.get("shares") or ""),
            str(payload.get("value") or ""),
            str(payload.get("status") or ""),
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]
