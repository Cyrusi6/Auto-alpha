"""Matching helpers for external and internal account records."""

from __future__ import annotations

from typing import Any


def fill_key(record: dict[str, Any]) -> str:
    for field in ("broker_fill_id", "external_fill_id"):
        value = str(record.get(field) or "")
        if value:
            return f"{field}:{value}"
    broker_order_id = str(record.get("broker_order_id") or "")
    if broker_order_id:
        return "|".join(
            [
                "order",
                broker_order_id,
                str(record.get("ts_code") or ""),
                str(record.get("side") or ""),
                str(record.get("shares") or ""),
                str(record.get("price") or ""),
                str(record.get("trade_date") or ""),
            ]
        )
    return "|".join(
        [
            "client",
            str(record.get("client_order_id") or ""),
            str(record.get("ts_code") or ""),
            str(record.get("side") or ""),
            str(record.get("shares") or ""),
            str(record.get("trade_date") or ""),
        ]
    )


def order_key(record: dict[str, Any]) -> str:
    for field in ("broker_order_id", "external_order_id", "client_order_id", "child_order_id"):
        value = str(record.get(field) or "")
        if value:
            return f"{field}:{value}"
    return ""


def position_key(record: dict[str, Any]) -> str:
    return str(record.get("ts_code") or "")


def settlement_key(record: dict[str, Any]) -> str:
    for field in ("source_id", "broker_fill_id", "external_settlement_id", "settlement_event_id"):
        value = str(record.get(field) or "")
        if value:
            return f"{field}:{value}"
    return "|".join([str(record.get("ts_code") or ""), str(record.get("event_type") or ""), str(record.get("settlement_date") or "")])


def corporate_action_key(record: dict[str, Any]) -> str:
    for field in ("action_id", "external_action_id"):
        value = str(record.get(field) or "")
        if value:
            return f"{field}:{value}"
    return "|".join([str(record.get("ts_code") or ""), str(record.get("trade_date") or ""), str(record.get("event_type") or "")])


def index_by(records: list[dict[str, Any]], key_fn) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        key = key_fn(record)
        if key:
            result[key] = record
    return result
