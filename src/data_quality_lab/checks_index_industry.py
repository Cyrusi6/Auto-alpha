"""Semantic checks for index, industry and status datasets."""

from __future__ import annotations

from typing import Any

from .rules import IssueCollector, as_float, duplicate_key_issues, is_valid_date


def run_index_industry_checks(
    datasets: dict[str, list[dict[str, Any]]],
    collector: IssueCollector,
    securities_by_code: dict[str, dict[str, Any]],
) -> None:
    duplicate_key_issues("index_basic", datasets.get("index_basic", []), ["index_code"], collector, "index_members.primary_key_unique")
    duplicate_key_issues("industry_classification", datasets.get("industry_classification", []), ["industry_code"], collector, "index_members.primary_key_unique")
    for dataset in ["index_daily_bars", "index_daily_basic"]:
        for record in datasets.get(dataset, []):
            trade_date = str(record.get("trade_date") or "")
            for field in ["open", "high", "low", "close"]:
                value = as_float(record.get(field))
                if value is not None and value < 0:
                    collector.add("daily_bars.ohlc_non_negative", dataset, f"{field} is negative", key=trade_date, field=field, sample=record)
    for record in datasets.get("industry_members", []):
        ts_code = str(record.get("ts_code") or "")
        if securities_by_code and ts_code and ts_code not in securities_by_code:
            collector.add("cross.unknown_security", "industry_members", "industry member ts_code not found in securities", key=ts_code, sample=record)
        for field in ["in_date", "out_date"]:
            value = record.get(field)
            if value and not is_valid_date(value):
                collector.add("events.valid_event_date", "industry_members", f"invalid {field}", key=ts_code, field=field, sample=record)
        if not (record.get("in_date") or record.get("out_date")):
            collector.add("events.valid_event_date", "industry_members", "industry member has no effective date", key=ts_code, sample=record)
    for dataset in ["suspensions", "name_changes", "new_shares"]:
        for record in datasets.get(dataset, []):
            ts_code = str(record.get("ts_code") or "")
            if securities_by_code and ts_code and ts_code not in securities_by_code:
                collector.add("cross.unknown_security", dataset, "status dataset ts_code not found in securities", key=ts_code, sample=record)
