"""Exposure helpers for pre-trade order risk checks."""

from __future__ import annotations

from typing import Any


def normalize_order(payload: Any, index: int = 0) -> dict[str, Any]:
    if hasattr(payload, "to_dict"):
        row = dict(payload.to_dict())
    elif hasattr(payload, "__dataclass_fields__"):
        row = {field: getattr(payload, field) for field in payload.__dataclass_fields__}
    else:
        row = dict(payload)
    order_id = (
        row.get("order_id")
        or row.get("child_order_id")
        or row.get("parent_order_id")
        or row.get("client_order_id")
        or f"order_{index + 1}"
    )
    row["order_id"] = str(order_id)
    row["trade_date"] = str(row.get("trade_date") or "")
    row["ts_code"] = str(row.get("ts_code") or "")
    row["side"] = str(row.get("side") or "").upper()
    row["order_value"] = _float(row.get("order_value") or row.get("requested_value") or row.get("value"))
    row["shares"] = int(_float(row.get("shares") or row.get("requested_shares")))
    row["price"] = _float(row.get("price"))
    row["target_weight"] = _float(row.get("target_weight"))
    return row


def order_exposure(orders: list[dict[str, Any]]) -> dict[str, float]:
    gross = sum(abs(float(order.get("order_value", 0.0) or 0.0)) for order in orders)
    buys = sum(float(order.get("order_value", 0.0) or 0.0) for order in orders if str(order.get("side") or "").upper() == "BUY")
    sells = sum(float(order.get("order_value", 0.0) or 0.0) for order in orders if str(order.get("side") or "").upper() == "SELL")
    return {
        "order_count": float(len(orders)),
        "gross_order_value": float(gross),
        "gross_buy_value": float(max(buys, 0.0)),
        "gross_sell_value": float(max(sells, 0.0)),
        "net_order_value": float(buys - sells),
        "max_order_value": max((abs(float(order.get("order_value", 0.0) or 0.0)) for order in orders), default=0.0),
    }


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
