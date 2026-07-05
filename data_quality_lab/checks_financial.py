"""Semantic checks for financial statement datasets."""

from __future__ import annotations

from typing import Any

from .rule_registry import FINANCIAL_STATEMENT_DATASETS
from .rules import IssueCollector, as_float, duplicate_key_issues, is_valid_date, record_key


def run_financial_statement_checks(datasets: dict[str, list[dict[str, Any]]], collector: IssueCollector) -> None:
    for dataset in FINANCIAL_STATEMENT_DATASETS:
        records = datasets.get(dataset, [])
        duplicate_key_issues(dataset, records, ["ts_code", "end_date", "ann_date", "report_type", "update_flag"], collector, "statements.pit_ann_date")
        for record in records:
            key = record_key(record, ["ts_code", "end_date", "ann_date"])
            ann_date = str(record.get("ann_date") or "")
            end_date = str(record.get("end_date") or "")
            if not ann_date:
                collector.add("statements.pit_ann_date", dataset, "missing ann_date creates unsafe PIT statement row", key=key, field="ann_date", sample=record)
            elif not is_valid_date(ann_date):
                collector.add("statements.pit_ann_date", dataset, "invalid ann_date", key=key, field="ann_date", sample=record)
            if end_date and ann_date and end_date > ann_date:
                collector.add("statements.pit_ann_date", dataset, "end_date is after ann_date", key=key, sample=record)
            for field in ["total_assets", "total_liab", "revenue", "n_income", "net_profit"]:
                value = as_float(record.get(field))
                if value is not None and abs(value) > 1e14:
                    collector.add("statements.pit_ann_date", dataset, f"{field} is extreme", severity="warning", key=key, field=field, sample=record)

    for dataset in ["earnings_forecasts", "earnings_express", "disclosure_calendar", "financial_audit", "main_business"]:
        for record in datasets.get(dataset, []):
            if not (record.get("ann_date") or record.get("disclosure_date") or record.get("publish_date")):
                collector.add("events.valid_event_date", dataset, "financial event row lacks availability date", key=record_key(record, ["ts_code", "end_date"]), field="ann_date", sample=record)
