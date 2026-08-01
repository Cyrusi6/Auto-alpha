"""Field mapping and normalization helpers for broker statements."""

from __future__ import annotations

from typing import Any

from .models import BrokerStatementParseIssue, BrokerStatementSchema


FLOAT_FIELDS = {
    "price",
    "value",
    "commission",
    "stamp_duty",
    "transfer_fee",
    "slippage",
    "market_impact",
    "other_fee",
    "total_fee",
    "cash_balance",
    "available_cash",
    "withdrawable_cash",
    "frozen_cash",
    "unsettled_receivable",
    "unsettled_payable",
    "cost_basis",
    "market_value",
    "realized_pnl",
    "unrealized_pnl",
}

INT_FIELDS = {"shares", "position_shares", "available_shares"}
DATE_FIELDS = {"trade_date", "as_of_date", "settlement_date", "available_date"}


def normalize_record(
    dataset: str,
    row: dict[str, Any],
    schema: BrokerStatementSchema,
    *,
    account_id: str | None,
    broker_name: str | None,
    trade_date: str | None,
    as_of_date: str | None,
    file_name: str,
    line_number: int | None,
) -> tuple[dict[str, Any], list[BrokerStatementParseIssue]]:
    mapping = schema.field_mapping.get(dataset, {})
    issues: list[BrokerStatementParseIssue] = []
    normalized: dict[str, Any] = {}
    for target, source in mapping.items():
        if source in row and row.get(source) not in {"", None}:
            normalized[target] = row.get(source)
    normalized.setdefault("account_id", account_id or row.get("account_id") or "")
    normalized.setdefault("broker_name", broker_name or row.get("broker_name") or "")
    normalized.setdefault("trade_date", trade_date or row.get("trade_date") or "")
    normalized.setdefault("as_of_date", as_of_date or row.get("as_of_date") or normalized.get("trade_date") or "")
    normalized["side"] = _normalize_side(normalized.get("side"))
    normalized["status"] = _normalize_status(normalized.get("status"))
    for field in DATE_FIELDS:
        if field in normalized:
            normalized[field] = _normalize_date(normalized.get(field))
    for field in FLOAT_FIELDS:
        if field in normalized:
            value, issue = _to_float(normalized.get(field), field, file_name, line_number)
            normalized[field] = value
            if issue:
                issues.append(issue)
    for field in INT_FIELDS:
        if field in normalized:
            value, issue = _to_int(normalized.get(field), field, file_name, line_number)
            normalized[field] = value
            if issue:
                issues.append(issue)
    if dataset in {"fills", "trades"}:
        normalized.setdefault("total_fee", _fee_total(normalized))
    return normalized, issues


def _normalize_side(value: Any) -> str:
    text = str(value or "").strip().upper()
    aliases = {"B": "BUY", "BUY": "BUY", "买": "BUY", "买入": "BUY", "S": "SELL", "SELL": "SELL", "卖": "SELL", "卖出": "SELL"}
    return aliases.get(text, text)


def _normalize_status(value: Any) -> str:
    text = str(value or "").strip().upper()
    aliases = {
        "成交": "FILLED",
        "已成": "FILLED",
        "FILLED": "FILLED",
        "PARTIAL": "PARTIAL",
        "部分成交": "PARTIAL",
        "REJECTED": "REJECTED",
        "废单": "REJECTED",
        "CANCELLED": "CANCELLED",
        "已撤": "CANCELLED",
    }
    return aliases.get(text, text)


def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if "-" in text:
        parts = text.split("T", 1)[0].split("-")
        if len(parts) == 3:
            return "".join(parts)
    return text


def _to_float(value: Any, field: str, file_name: str, line_number: int | None) -> tuple[float, BrokerStatementParseIssue | None]:
    try:
        if value in {"", None}:
            return 0.0, None
        return float(value), None
    except (TypeError, ValueError):
        return 0.0, BrokerStatementParseIssue("error", "malformed_number", f"field {field} is not numeric", file_name, line_number)


def _to_int(value: Any, field: str, file_name: str, line_number: int | None) -> tuple[int, BrokerStatementParseIssue | None]:
    try:
        if value in {"", None}:
            return 0, None
        return int(float(value)), None
    except (TypeError, ValueError):
        return 0, BrokerStatementParseIssue("error", "malformed_number", f"field {field} is not integer", file_name, line_number)


def _fee_total(row: dict[str, Any]) -> float:
    if float(row.get("total_fee", 0.0) or 0.0):
        return float(row.get("total_fee", 0.0) or 0.0)
    fields = ["commission", "stamp_duty", "transfer_fee", "slippage", "market_impact", "other_fee"]
    return float(sum(float(row.get(field, 0.0) or 0.0) for field in fields))
