"""Semantic checks for flow, margin, trading and event datasets."""

from __future__ import annotations

from typing import Any

from .rules import IssueCollector, as_float, duplicate_key_issues, is_valid_date, record_key


def run_event_checks(
    datasets: dict[str, list[dict[str, Any]]],
    collector: IssueCollector,
    securities_by_code: dict[str, dict[str, Any]],
) -> None:
    _check_moneyflow(datasets.get("moneyflow", []), collector, securities_by_code)
    _check_margin(datasets, collector, securities_by_code)
    _check_abnormal_trading(datasets, collector, securities_by_code)
    _check_holder_event_risk(datasets, collector, securities_by_code)


def _check_moneyflow(records: list[dict[str, Any]], collector: IssueCollector, securities_by_code: dict[str, dict[str, Any]]) -> None:
    duplicate_key_issues("moneyflow", records, ["ts_code", "trade_date"], collector, "daily_basic.primary_key_unique")
    for record in records:
        ts_code = str(record.get("ts_code") or "")
        if securities_by_code and ts_code and ts_code not in securities_by_code:
            collector.add("cross.unknown_security", "moneyflow", "moneyflow ts_code not found in securities", key=ts_code, sample=record)


def _check_margin(datasets: dict[str, list[dict[str, Any]]], collector: IssueCollector, securities_by_code: dict[str, dict[str, Any]]) -> None:
    for dataset in ["margin_summary", "margin_detail"]:
        records = datasets.get(dataset, [])
        key_fields = ["ts_code", "trade_date"] if dataset == "margin_detail" else ["exchange", "trade_date"]
        duplicate_key_issues(dataset, records, key_fields, collector, "daily_basic.primary_key_unique")
        for record in records:
            ts_code = str(record.get("ts_code") or "")
            if ts_code and securities_by_code and ts_code not in securities_by_code:
                collector.add("cross.unknown_security", dataset, "margin ts_code not found in securities", key=ts_code, sample=record)
            for field in ["rzye", "rqye", "rzmre", "rqmcl", "rzrqye", "fin_balance", "sec_balance"]:
                value = as_float(record.get(field))
                if value is not None and value < 0:
                    collector.add("daily_basic.non_negative", dataset, f"{field} is negative", key=record_key(record, key_fields), field=field, sample=record)


def _check_abnormal_trading(datasets: dict[str, list[dict[str, Any]]], collector: IssueCollector, securities_by_code: dict[str, dict[str, Any]]) -> None:
    for dataset in ["top_list", "top_inst", "block_trades"]:
        for record in datasets.get(dataset, []):
            ts_code = str(record.get("ts_code") or "")
            if ts_code and securities_by_code and ts_code not in securities_by_code:
                collector.add("cross.unknown_security", dataset, "event ts_code not found in securities", key=ts_code, sample=record)
            if not (record.get("trade_date") or record.get("ann_date") or record.get("ts_code")):
                collector.add("events.valid_event_date", dataset, "event row lacks date or ts_code", sample=record)
            for field in ["price", "vol", "amount"]:
                value = as_float(record.get(field))
                if value is not None and value < 0:
                    collector.add("daily_basic.non_negative", dataset, f"{field} is negative", field=field, sample=record)


def _check_holder_event_risk(datasets: dict[str, list[dict[str, Any]]], collector: IssueCollector, securities_by_code: dict[str, dict[str, Any]]) -> None:
    for dataset in ["holder_number", "holder_trades", "top10_holders", "top10_float_holders", "pledge_detail", "pledge_stat", "repurchases", "share_unlocks", "hk_holdings"]:
        for record in datasets.get(dataset, []):
            ts_code = str(record.get("ts_code") or "")
            if ts_code and securities_by_code and ts_code not in securities_by_code:
                collector.add("cross.unknown_security", dataset, "holder/event ts_code not found in securities", key=ts_code, sample=record)
            date_fields = ["ann_date", "end_date", "trade_date", "float_date", "change_date"]
            if not any(record.get(field) for field in date_fields):
                collector.add("events.valid_event_date", dataset, "row lacks availability/event date", key=ts_code, sample=record)
            for field in ["holder_num", "hold_ratio", "pledge_ratio", "amount", "vol", "unlock_amount", "holding_amount", "holding_ratio"]:
                value = as_float(record.get(field))
                if value is not None and value < 0:
                    collector.add("daily_basic.non_negative", dataset, f"{field} is negative", key=ts_code, field=field, sample=record)
            for field in ["ann_date", "float_date", "trade_date", "change_date"]:
                value = record.get(field)
                if value and not is_valid_date(value):
                    collector.add("events.valid_event_date", dataset, f"invalid {field}", key=ts_code, field=field, sample=record)
