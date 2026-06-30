"""Mappings between internal orders and broker file records."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

from broker_adapter.models import BrokerFillRecord, BrokerOrderRequest

from .models import BrokerFileProfile, BrokerFileRecord, BrokerFileRoundTripIssue
from .profiles import INTERNAL_FIELDS


def map_internal_orders_to_file_records(
    orders: Iterable[dict[str, Any]],
    profile: BrokerFileProfile,
    *,
    broker_batch_id: str = "",
    production_run_id: str = "",
    trade_date: str = "",
) -> tuple[list[BrokerFileRecord], list[BrokerFileRoundTripIssue]]:
    records: list[BrokerFileRecord] = []
    issues: list[BrokerFileRoundTripIssue] = []
    for order in orders:
        try:
            records.append(_record_from_payload(order, broker_batch_id=broker_batch_id, production_run_id=production_run_id, trade_date=trade_date))
        except Exception as exc:
            issues.append(BrokerFileRoundTripIssue("error", "mapping_error", str(exc), {"payload": dict(order)}))
    return records, issues


def map_child_orders_to_file_records(
    child_orders: Iterable[dict[str, Any]],
    profile: BrokerFileProfile,
    *,
    broker_batch_id: str = "",
    production_run_id: str = "",
    trade_date: str = "",
) -> tuple[list[BrokerFileRecord], list[BrokerFileRoundTripIssue]]:
    return map_internal_orders_to_file_records(child_orders, profile, broker_batch_id=broker_batch_id, production_run_id=production_run_id, trade_date=trade_date)


def map_broker_requests_to_file_records(
    requests: Iterable[BrokerOrderRequest | dict[str, Any]],
    profile: BrokerFileProfile,
) -> tuple[list[BrokerFileRecord], list[BrokerFileRoundTripIssue]]:
    rows = [request.to_dict() if hasattr(request, "to_dict") else dict(request) for request in requests]
    return map_internal_orders_to_file_records(rows, profile)


def file_record_to_row(record: BrokerFileRecord, profile: BrokerFileProfile) -> dict[str, Any]:
    payload = record.to_dict()
    row = {}
    for field in INTERNAL_FIELDS:
        value = payload.get(field)
        if field == "side":
            value = profile.side_mapping.get(str(value).upper(), value)
        if field == "price":
            value = round(float(value or 0.0), profile.price_precision)
        if field == "order_value":
            value = round(float(value or 0.0) / float(profile.amount_unit or 1.0), profile.value_precision)
        row[profile.field_mapping.get(field, field)] = value
    return row


def row_to_internal(row: dict[str, Any], profile: BrokerFileProfile) -> dict[str, Any]:
    reverse = {value: key for key, value in profile.field_mapping.items()}
    payload = {reverse.get(key, key): value for key, value in row.items()}
    side_reverse = {value: key for key, value in profile.side_mapping.items()}
    if "side" in payload:
        payload["side"] = side_reverse.get(str(payload["side"]), str(payload["side"]).upper())
    return payload


def map_file_fills_to_broker_fills(rows: Iterable[dict[str, Any]], profile: BrokerFileProfile, batch_id: str) -> list[BrokerFillRecord]:
    fills: list[BrokerFillRecord] = []
    for row in rows:
        payload = row_to_internal(row, profile)
        client_order_id = str(payload.get("client_order_id") or "")
        shares = int(float(payload.get("shares") or 0))
        price = float(payload.get("price") or 0.0)
        value = float(payload.get("value") or payload.get("order_value") or shares * price)
        status = str(payload.get("status") or "FILLED").upper()
        fills.append(
            BrokerFillRecord(
                broker_fill_id=str(payload.get("broker_fill_id") or "bff_" + _hash(batch_id, client_order_id, shares, value)),
                broker_order_id=str(payload.get("broker_order_id") or client_order_id),
                client_order_id=client_order_id,
                batch_id=batch_id,
                trade_date=str(payload.get("trade_date") or ""),
                ts_code=str(payload.get("ts_code") or ""),
                side=str(payload.get("side") or ""),
                price=price,
                shares=shares,
                value=value,
                cost=float(payload.get("cost") or 0.0),
                status=status,
                reason=str(payload.get("reason") or ""),
                parent_order_id=payload.get("parent_order_id"),
                child_order_id=payload.get("child_order_id"),
                bucket=payload.get("bucket"),
                broker_adapter="file",
            )
        )
    return fills


def _record_from_payload(payload: dict[str, Any], *, broker_batch_id: str, production_run_id: str, trade_date: str) -> BrokerFileRecord:
    ts_code = str(payload.get("ts_code") or "")
    side = str(payload.get("side") or "").upper()
    order_value = float(payload.get("order_value") or payload.get("value") or 0.0)
    price = float(payload.get("price") or 0.0)
    shares = int(float(payload.get("shares") or (order_value / price if price > 0 else 0)))
    effective_trade_date = str(payload.get("trade_date") or trade_date)
    child_order_id = payload.get("child_order_id")
    client_order_id = str(payload.get("client_order_id") or child_order_id or "co_" + _hash(broker_batch_id, ts_code, side, order_value))
    if not ts_code or not side:
        raise ValueError("ts_code and side are required")
    return BrokerFileRecord(
        client_order_id=client_order_id,
        trade_date=effective_trade_date,
        ts_code=ts_code,
        side=side,
        shares=max(shares, 0),
        price=price,
        price_type=str(payload.get("price_type") or "MARKET"),
        order_value=order_value,
        parent_order_id=payload.get("parent_order_id"),
        child_order_id=child_order_id,
        bucket=payload.get("bucket"),
        broker_batch_id=str(payload.get("broker_batch_id") or broker_batch_id),
        production_run_id=str(payload.get("production_run_id") or production_run_id),
        metadata={"source_reason": payload.get("reason", "")},
    )


def _hash(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:20]
