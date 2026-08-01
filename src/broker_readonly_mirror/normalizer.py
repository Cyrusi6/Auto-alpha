"""Normalize read-only broker payloads into mirror records."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from .models import (
    BrokerReadonlyCash,
    BrokerReadonlyFill,
    BrokerReadonlyMirrorIssue,
    BrokerReadonlyOrder,
    BrokerReadonlyPosition,
    BrokerReadonlyStatement,
)


def normalize_readonly_payload(
    payload: dict[str, Any],
    *,
    account_id: str,
    broker_name: str,
    trade_date: str,
    as_of_date: str,
) -> dict[str, Any]:
    issues: list[BrokerReadonlyMirrorIssue] = []
    account = _with_defaults(payload.get("account_snapshot") or payload.get("cash") or {}, account_id, broker_name, trade_date, as_of_date)
    cash = BrokerReadonlyCash(
        account_id=str(account.get("account_id") or account_id),
        broker_name=str(account.get("broker_name") or broker_name),
        trade_date=str(account.get("trade_date") or trade_date),
        as_of_date=str(account.get("as_of_date") or as_of_date),
        cash_balance=_float(account.get("cash_balance")),
        available_cash=_float(account.get("available_cash")),
        withdrawable_cash=_float(account.get("withdrawable_cash")),
        frozen_cash=_float(account.get("frozen_cash")),
        metadata=dict(account.get("metadata") or {}),
    )
    positions = [_position(row, account_id, broker_name, trade_date, as_of_date, issues) for row in _rows(payload, "positions")]
    orders = [_order(row, account_id, broker_name, trade_date, as_of_date, issues, index) for index, row in enumerate(_rows(payload, "orders"), start=1)]
    fills = [_fill(row, account_id, broker_name, trade_date, as_of_date, issues, index) for index, row in enumerate(_rows(payload, "fills"), start=1)]
    statements = [_statement(row, account_id, broker_name, trade_date, as_of_date, issues) for row in _rows(payload, "statements")]
    source_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {
        "cash": cash,
        "positions": positions,
        "orders": orders,
        "fills": fills,
        "statements": statements,
        "issues": issues,
        "source_hash": source_hash,
    }


def to_statement_artifacts(normalized: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "cash": [normalized["cash"].to_dict()] if normalized.get("cash") else [],
        "positions": [item.to_dict() for item in normalized.get("positions", [])],
        "orders": [item.to_dict() for item in normalized.get("orders", [])],
        "fills": [item.to_dict() for item in normalized.get("fills", [])],
        "trades": [item.to_dict() for item in normalized.get("fills", [])],
        "settlements": [item.to_dict() for item in normalized.get("statements", [])],
    }


def _position(row: dict[str, Any], account_id: str, broker_name: str, trade_date: str, as_of_date: str, issues: list[BrokerReadonlyMirrorIssue]) -> BrokerReadonlyPosition:
    row = _with_defaults(row, account_id, broker_name, trade_date, as_of_date)
    if not row.get("ts_code"):
        issues.append(BrokerReadonlyMirrorIssue("warning", "missing_ts_code", "readonly position row is missing ts_code"))
    return BrokerReadonlyPosition(
        account_id=str(row.get("account_id") or account_id),
        broker_name=str(row.get("broker_name") or broker_name),
        trade_date=str(row.get("trade_date") or trade_date),
        as_of_date=str(row.get("as_of_date") or as_of_date),
        ts_code=str(row.get("ts_code") or ""),
        position_shares=_int(row.get("position_shares") if row.get("position_shares") is not None else row.get("shares")),
        available_shares=_int(row.get("available_shares")),
        cost_basis=_float(row.get("cost_basis")),
        market_value=_float(row.get("market_value")),
        metadata=dict(row.get("metadata") or {}),
    )


def _order(row: dict[str, Any], account_id: str, broker_name: str, trade_date: str, as_of_date: str, issues: list[BrokerReadonlyMirrorIssue], index: int) -> BrokerReadonlyOrder:
    row = _with_defaults(row, account_id, broker_name, trade_date, as_of_date)
    external_id = str(row.get("external_order_id") or row.get("broker_order_id") or f"readonly_order_{index}")
    if not row.get("ts_code"):
        issues.append(BrokerReadonlyMirrorIssue("warning", "missing_order_ts_code", "readonly order row is missing ts_code", {"external_order_id": external_id}))
    return BrokerReadonlyOrder(
        account_id=str(row.get("account_id") or account_id),
        broker_name=str(row.get("broker_name") or broker_name),
        trade_date=str(row.get("trade_date") or trade_date),
        as_of_date=str(row.get("as_of_date") or as_of_date),
        external_order_id=external_id,
        broker_order_id=str(row.get("broker_order_id") or external_id),
        client_order_id=str(row.get("client_order_id") or ""),
        ts_code=str(row.get("ts_code") or ""),
        side=str(row.get("side") or ""),
        price=_float(row.get("price")),
        shares=_int(row.get("shares")),
        value=_float(row.get("value")),
        status=str(row.get("status") or ""),
        metadata=dict(row.get("metadata") or {}),
    )


def _fill(row: dict[str, Any], account_id: str, broker_name: str, trade_date: str, as_of_date: str, issues: list[BrokerReadonlyMirrorIssue], index: int) -> BrokerReadonlyFill:
    row = _with_defaults(row, account_id, broker_name, trade_date, as_of_date)
    external_id = str(row.get("external_fill_id") or row.get("broker_fill_id") or f"readonly_fill_{index}")
    if not row.get("ts_code"):
        issues.append(BrokerReadonlyMirrorIssue("warning", "missing_fill_ts_code", "readonly fill row is missing ts_code", {"external_fill_id": external_id}))
    return BrokerReadonlyFill(
        account_id=str(row.get("account_id") or account_id),
        broker_name=str(row.get("broker_name") or broker_name),
        trade_date=str(row.get("trade_date") or trade_date),
        as_of_date=str(row.get("as_of_date") or as_of_date),
        external_fill_id=external_id,
        broker_fill_id=str(row.get("broker_fill_id") or external_id),
        broker_order_id=str(row.get("broker_order_id") or ""),
        client_order_id=str(row.get("client_order_id") or ""),
        ts_code=str(row.get("ts_code") or ""),
        side=str(row.get("side") or ""),
        price=_float(row.get("price")),
        shares=_int(row.get("shares")),
        value=_float(row.get("value")),
        commission=_float(row.get("commission")),
        stamp_duty=_float(row.get("stamp_duty")),
        transfer_fee=_float(row.get("transfer_fee")),
        total_fee=_float(row.get("total_fee")),
        status=str(row.get("status") or ""),
        metadata=dict(row.get("metadata") or {}),
    )


def _statement(row: dict[str, Any], account_id: str, broker_name: str, trade_date: str, as_of_date: str, issues: list[BrokerReadonlyMirrorIssue]) -> BrokerReadonlyStatement:
    row = _with_defaults(row, account_id, broker_name, trade_date, as_of_date)
    statement_id = str(row.get("statement_id") or f"readonly_statement_{as_of_date}")
    return BrokerReadonlyStatement(
        account_id=str(row.get("account_id") or account_id),
        broker_name=str(row.get("broker_name") or broker_name),
        trade_date=str(row.get("trade_date") or trade_date),
        as_of_date=str(row.get("as_of_date") or as_of_date),
        statement_id=statement_id,
        cash_balance=_float(row.get("cash_balance")),
        position_count=_int(row.get("position_count")),
        fill_count=_int(row.get("fill_count")),
        metadata=dict(row.get("metadata") or {}),
    )


def _rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = payload.get(key) or []
    return [dict(item) for item in rows if isinstance(item, dict)]


def _with_defaults(row: Any, account_id: str, broker_name: str, trade_date: str, as_of_date: str) -> dict[str, Any]:
    payload = dict(row) if isinstance(row, dict) else {}
    payload.setdefault("account_id", account_id)
    payload.setdefault("broker_name", broker_name)
    payload.setdefault("trade_date", trade_date)
    payload.setdefault("as_of_date", as_of_date)
    return payload


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
