"""Validation checks for normalized broker statements."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import BrokerStatementParseIssue, BrokerStatementValidationReport


def validate_statement(
    statement_id: str,
    normalized: dict[str, list[dict[str, Any]]],
    parse_issues: list[BrokerStatementParseIssue],
    *,
    as_of_date: str,
    strict: bool = False,
) -> BrokerStatementValidationReport:
    issues = list(parse_issues)
    counts = {dataset: len(rows) for dataset, rows in normalized.items()}
    if not normalized.get("cash"):
        issues.append(BrokerStatementParseIssue("error" if strict else "warning", "missing_cash_statement", "cash statement is missing"))
    if not normalized.get("positions"):
        issues.append(
            BrokerStatementParseIssue("error" if strict else "warning", "missing_position_statement", "position statement is missing")
        )
    _duplicate_id_check(issues, normalized.get("orders", []), "external_order_id", "duplicate_external_order_id")
    _duplicate_id_check(issues, normalized.get("trades", []), "external_trade_id", "duplicate_external_trade_id")
    _duplicate_id_check(issues, normalized.get("fills", []), "external_fill_id", "duplicate_external_fill_id")
    for dataset, rows in normalized.items():
        for index, row in enumerate(rows, start=1):
            if str(row.get("as_of_date") or "") and str(row.get("as_of_date")) != as_of_date:
                issues.append(
                    BrokerStatementParseIssue(
                        "warning",
                        "trade_date_as_of_date_mismatch",
                        f"{dataset} row has as_of_date {row.get('as_of_date')} instead of {as_of_date}",
                        line_number=index,
                    )
                )
            if dataset in {"orders", "trades", "fills"} and row.get("side") not in {"BUY", "SELL", ""}:
                issues.append(BrokerStatementParseIssue("error", "invalid_side", "side must be BUY or SELL", line_number=index))
            if dataset in {"orders", "trades", "fills"} and row.get("status") not in {"FILLED", "PARTIAL", "REJECTED", "CANCELLED", ""}:
                issues.append(BrokerStatementParseIssue("warning", "invalid_status", "status is not a known local broker status", line_number=index))
            if dataset == "positions" and int(row.get("position_shares", 0) or 0) < 0:
                issues.append(BrokerStatementParseIssue("error", "negative_position_shares", "position shares cannot be negative", line_number=index))
            if dataset == "cash" and float(row.get("cash_balance", 0.0) or 0.0) < 0:
                issues.append(BrokerStatementParseIssue("warning", "negative_cash_balance", "cash balance is negative", line_number=index))
    error_count = sum(1 for issue in issues if issue.severity in {"error", "blocker"})
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    status = "error" if error_count else ("warning" if warning_count else "ok")
    return BrokerStatementValidationReport(
        statement_id=statement_id,
        status=status,
        issue_count=len(issues),
        error_count=error_count,
        warning_count=warning_count,
        issues=issues,
        dataset_counts=counts,
    )


def _duplicate_id_check(issues: list[BrokerStatementParseIssue], rows: list[dict[str, Any]], field: str, code: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        value = str(row.get(field) or "")
        if not value:
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    for value in sorted(duplicates):
        issues.append(BrokerStatementParseIssue("error", code, f"duplicate external id: {value}", metadata={"field": field, "value": value}))


def issue_counts_by_code(issues: list[BrokerStatementParseIssue]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.code] = counts.get(issue.code, 0) + 1
    return counts


def validate_statement_dir(path: str | Path, statement_id: str = "statement", as_of_date: str = "", strict: bool = False) -> BrokerStatementValidationReport:
    from .importer import read_normalized_statement

    return validate_statement(statement_id, read_normalized_statement(path), [], as_of_date=as_of_date, strict=strict)
