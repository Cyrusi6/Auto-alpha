"""Settlement artifact writers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .engine import update_cash_buckets, update_position_availability
from .performance import build_account_nav_series, compute_account_performance
from .reconciliation import reconcile_account_state
from .models import SettlementReport


def write_settlement_report(account_state, output_dir: str | Path, as_of_date: str, prices_by_date: dict[str, dict[str, float]] | None = None, profile_name: str = "cn_ashare_paper_default") -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    events = [dict(event) for event in getattr(account_state, "settlement_events", []) or account_state.settlement_ledger]
    cash_buckets = update_cash_buckets(account_state, as_of_date)
    availability = update_position_availability(account_state, as_of_date)
    nav_records = build_account_nav_series(account_state, prices_by_date=prices_by_date)
    performance = compute_account_performance(nav_records)
    reconciliation = reconcile_account_state(account_state, events, getattr(account_state, "position_lots", []), nav_records=nav_records, as_of_date=as_of_date)
    fee_tax = _fee_tax_summary(events)
    report = SettlementReport(
        account_id=account_state.account_id,
        as_of_date=as_of_date,
        settlement_aware=True,
        settlement_profile=profile_name,
        pending_settlement_event_count=sum(1 for event in events if event.get("status") == "pending"),
        failed_settlement_event_count=sum(1 for event in events if event.get("status") == "failed"),
        cash_buckets=cash_buckets.to_dict(),
        position_count=len(account_state.positions),
        position_lot_count=len(getattr(account_state, "position_lots", []) or []),
        realized_pnl=float(sum(record.get("realized_pnl", 0.0) for record in getattr(account_state, "realized_pnl_ledger", []) or [])),
        unrealized_pnl=float(sum(position.unrealized_pnl for position in account_state.positions.values())),
        nav_difference=float(reconciliation.nav_difference),
        fee_tax_total=float(fee_tax["fee_tax_total"]),
        reconciliation_error_count=sum(1 for issue in reconciliation.issues if issue.severity in {"error", "blocker"}),
    )
    paths = {
        "settlement_report_path": str(write_json_artifact(target / "settlement_report.json", report.to_dict(), "settlement_report", "settlement_engine")),
        "settlement_events_path": str(write_jsonl_artifact(target / "settlement_events.jsonl", events, "settlement_events", "settlement_engine")),
        "cash_buckets_path": str(write_jsonl_artifact(target / "cash_buckets.jsonl", [cash_buckets.to_dict()], "cash_buckets", "settlement_engine")),
        "position_lots_path": str(write_jsonl_artifact(target / "position_lots.jsonl", getattr(account_state, "position_lots", []) or [], "position_lots", "settlement_engine")),
        "position_availability_path": str(write_jsonl_artifact(target / "position_availability.jsonl", [record.to_dict() for record in availability], "position_availability", "settlement_engine")),
        "realized_pnl_path": str(write_jsonl_artifact(target / "realized_pnl.jsonl", getattr(account_state, "realized_pnl_ledger", []) or [], "realized_pnl", "settlement_engine")),
        "account_nav_path": str(write_jsonl_artifact(target / "account_nav.jsonl", [record.to_dict() for record in nav_records], "account_nav", "settlement_engine")),
        "account_performance_report_path": str(write_json_artifact(target / "account_performance_report.json", performance, "account_performance_report", "settlement_engine")),
        "account_reconciliation_report_path": str(write_json_artifact(target / "account_reconciliation_report.json", reconciliation.to_dict(), "account_reconciliation_report", "settlement_engine")),
        "fee_tax_report_path": str(write_json_artifact(target / "fee_tax_report.json", fee_tax, "fee_tax_report", "settlement_engine")),
    }
    report_payload = report.to_dict() | {"paths": paths}
    write_json_artifact(target / "settlement_report.json", report_payload, "settlement_report", "settlement_engine")
    (target / "settlement_report.md").write_text(_markdown(report_payload, performance, reconciliation.to_dict()), encoding="utf-8")
    paths["settlement_report_md_path"] = str(target / "settlement_report.md")
    return paths


def _fee_tax_summary(events: list[dict[str, Any]]) -> dict[str, float]:
    keys = ["commission", "stamp_duty", "transfer_fee", "slippage", "market_impact", "other_fee", "total"]
    summary = {key: 0.0 for key in keys}
    for event in events:
        fee_tax = event.get("fee_tax") or {}
        for key in keys:
            summary[key] += float(fee_tax.get(key, 0.0) or 0.0)
    summary["fee_tax_total"] = summary["total"]
    summary["total_fee_tax"] = summary["total"]
    return summary


def _markdown(report: dict[str, Any], performance: dict[str, Any], reconciliation: dict[str, Any]) -> str:
    lines = [
        "# Settlement Report",
        "",
        f"- account_id: {report.get('account_id')}",
        f"- as_of_date: {report.get('as_of_date')}",
        f"- settlement_profile: {report.get('settlement_profile')}",
        f"- pending events: {report.get('pending_settlement_event_count')}",
        f"- failed events: {report.get('failed_settlement_event_count')}",
        f"- realized_pnl: {report.get('realized_pnl')}",
        f"- unrealized_pnl: {report.get('unrealized_pnl')}",
        f"- nav_difference: {report.get('nav_difference')}",
        "",
        "## Performance",
        "```json",
        json.dumps(performance, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Reconciliation",
        "```json",
        json.dumps(reconciliation, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    return "\n".join(lines) + "\n"
