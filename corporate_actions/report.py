"""Corporate action artifact report writers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import CorporateActionEvent, CorporateActionReport, CorporateActionValidationReport, TotalReturnSeriesRecord
from .reconciliation import reconcile_adjustment_factors_with_actions
from .schedule import build_action_schedule
from .total_return import build_total_return_series


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def build_corporate_action_validation_report(events: Sequence[CorporateActionEvent]) -> CorporateActionValidationReport:
    issues: list[dict[str, Any]] = []
    for event in events:
        if event.availability_date is None:
            issues.append({"severity": "warning", "code": "missing_availability_date", "action_id": event.action_id, "ts_code": event.ts_code})
        if event.effective_date is None:
            issues.append({"severity": "warning", "code": "missing_effective_date", "action_id": event.action_id, "ts_code": event.ts_code})
        if event.pay_date and event.ex_date and event.pay_date < event.ex_date:
            issues.append({"severity": "error", "code": "pay_date_before_ex_date", "action_id": event.action_id, "ts_code": event.ts_code})
        if event.record_date and event.ex_date and event.record_date > event.ex_date:
            issues.append({"severity": "warning", "code": "record_date_after_ex_date", "action_id": event.action_id, "ts_code": event.ts_code})
    return CorporateActionValidationReport(
        event_count=len(events),
        implemented_action_count=sum(event.action_type != "proposal_only" for event in events),
        proposal_action_count=sum(event.action_type == "proposal_only" for event in events),
        warning_count=sum(issue["severity"] == "warning" for issue in issues),
        error_count=sum(issue["severity"] == "error" for issue in issues),
        issues=issues,
    )


def write_corporate_action_report(
    data_dir: str | Path,
    events: Sequence[CorporateActionEvent],
    output_dir: str | Path,
    start_date: str,
    end_date: str,
    total_return_mode: str = "cash_reinvested",
    reconcile_adjustment: bool = False,
    tolerance: float = 0.05,
) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    validation = build_corporate_action_validation_report(events)
    schedule = build_action_schedule(events, start_date=start_date, end_date=end_date, include_proposals=True)
    total_return_records = build_total_return_series(data_dir, events, mode=total_return_mode)
    reconciliation = (
        reconcile_adjustment_factors_with_actions(data_dir, events, total_return_records, tolerance=tolerance)
        if reconcile_adjustment
        else {"tolerance": tolerance, "issue_count": 0, "warning_count": 0, "error_count": 0, "issues": []}
    )
    report = CorporateActionReport(
        created_at=utc_now(),
        data_dir=str(data_dir),
        event_count=len(events),
        implemented_action_count=sum(event.action_type != "proposal_only" for event in events),
        proposal_action_count=sum(event.action_type == "proposal_only" for event in events),
        cash_dividend_event_count=sum(event.cash_div_per_share > 0 for event in events if event.action_type != "proposal_only"),
        stock_distribution_event_count=sum(event.stock_distribution_ratio > 0 for event in events if event.action_type != "proposal_only"),
        combined_event_count=sum(event.action_type == "combined_distribution" for event in events),
        cash_dividend_amount_per_share=float(sum(event.cash_div_per_share for event in events if event.action_type != "proposal_only")),
        stock_distribution_ratio_sum=float(sum(event.stock_distribution_ratio for event in events if event.action_type != "proposal_only")),
        unprocessed_corporate_action_count=sum(event.action_type == "proposal_only" for event in events),
        corporate_action_warning_count=validation.warning_count,
        corporate_action_error_count=validation.error_count,
        total_return_mode=total_return_mode,
        adjustment_reconciliation_warning_count=int(reconciliation.get("warning_count", 0)),
        adjustment_reconciliation_error_count=int(reconciliation.get("error_count", 0)),
    )
    paths = {
        "corporate_actions_report_path": str(output / "corporate_actions_report.json"),
        "corporate_actions_report_md_path": str(output / "corporate_actions_report.md"),
        "corporate_action_events_path": str(output / "corporate_action_events.jsonl"),
        "corporate_action_schedule_path": str(output / "corporate_action_schedule.json"),
        "corporate_action_validation_report_path": str(output / "corporate_action_validation_report.json"),
        "adjustment_reconciliation_path": str(output / "adjustment_factor_reconciliation.json"),
        "total_return_series_path": str(output / "total_return_series.jsonl"),
        "total_return_report_path": str(output / "total_return_report.json"),
        "total_return_report_md_path": str(output / "total_return_report.md"),
    }
    report_payload = report.to_dict()
    report_payload["paths"] = paths
    write_json_artifact(paths["corporate_actions_report_path"], report_payload, "corporate_actions_report", "corporate_actions")
    write_jsonl_artifact(paths["corporate_action_events_path"], [event.to_dict() for event in events], "corporate_action_events", "corporate_actions")
    write_json_artifact(paths["corporate_action_schedule_path"], {"schedule": schedule, "records": len(schedule)}, "corporate_action_schedule", "corporate_actions")
    write_json_artifact(paths["corporate_action_validation_report_path"], validation.to_dict(), "corporate_action_validation_report", "corporate_actions")
    write_json_artifact(paths["adjustment_reconciliation_path"], reconciliation, "adjustment_factor_reconciliation", "corporate_actions")
    write_jsonl_artifact(paths["total_return_series_path"], [record.to_dict() for record in total_return_records], "total_return_series", "corporate_actions")
    total_return_payload = _total_return_report(total_return_records, total_return_mode)
    write_json_artifact(paths["total_return_report_path"], total_return_payload, "total_return_report", "corporate_actions")
    Path(paths["corporate_actions_report_md_path"]).write_text(_markdown_report(report_payload, validation.to_dict(), reconciliation), encoding="utf-8")
    Path(paths["total_return_report_md_path"]).write_text(_markdown_total_return(total_return_payload), encoding="utf-8")
    return paths


def _total_return_report(records: Sequence[TotalReturnSeriesRecord], mode: str) -> dict[str, Any]:
    return {
        "created_at": utc_now(),
        "total_return_mode": mode,
        "records": len(records),
        "action_days": sum(record.action_flag for record in records),
        "cash_dividend_amount": float(sum(record.cash_dividend for record in records)),
        "stock_distribution_ratio_sum": float(sum(record.stock_distribution_ratio for record in records)),
        "max_abs_total_return": float(max((abs(record.total_return) for record in records), default=0.0)),
    }


def _markdown_report(report: dict[str, Any], validation: dict[str, Any], reconciliation: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Corporate Actions Report",
            "",
            f"- events: {report['event_count']}",
            f"- implemented: {report['implemented_action_count']}",
            f"- proposals/skipped: {report['proposal_action_count']}",
            f"- warnings: {report['corporate_action_warning_count']}",
            f"- errors: {report['corporate_action_error_count']}",
            f"- total_return_mode: {report['total_return_mode']}",
            f"- adjustment reconciliation warnings: {reconciliation.get('warning_count', 0)}",
            f"- adjustment reconciliation errors: {reconciliation.get('error_count', 0)}",
            "",
            "This local report does not provide tax advice. Cash tax defaults to zero unless explicitly configured.",
        ]
    )


def _markdown_total_return(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Total Return Report",
            "",
            f"- mode: {payload['total_return_mode']}",
            f"- records: {payload['records']}",
            f"- action_days: {payload['action_days']}",
            f"- cash_dividend_amount: {payload['cash_dividend_amount']}",
            f"- stock_distribution_ratio_sum: {payload['stock_distribution_ratio_sum']}",
        ]
    )


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
