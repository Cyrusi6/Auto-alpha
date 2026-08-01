"""Semantic checks for core A-share datasets."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .rules import IssueCollector, as_float, duplicate_key_issues, is_valid_date, record_key


def check_securities(records: list[dict[str, Any]], collector: IssueCollector) -> None:
    duplicate_key_issues("securities", records, ["ts_code"], collector, "securities.ts_code_unique")
    statuses = {str(record.get("list_status") or "") for record in records}
    for record in records:
        ts_code = str(record.get("ts_code") or "")
        if not ts_code:
            collector.add("securities.ts_code_unique", "securities", "missing ts_code", sample=record)
        status = str(record.get("list_status") or "")
        if status and status not in {"L", "D", "P"}:
            collector.add("securities.list_status_valid", "securities", "invalid list_status", key=ts_code, field="list_status", sample=record)
        list_date = record.get("list_date")
        delist_date = record.get("delist_date")
        if list_date and not is_valid_date(list_date):
            collector.add("securities.list_date_valid", "securities", "invalid list_date", key=ts_code, field="list_date", sample=record)
        if delist_date and not is_valid_date(delist_date):
            collector.add("securities.list_date_valid", "securities", "invalid delist_date", key=ts_code, field="delist_date", sample=record)
        if list_date and delist_date and str(delist_date) < str(list_date):
            collector.add("securities.list_date_valid", "securities", "delist_date before list_date", key=ts_code, sample=record)
    if records and not (statuses & {"L"}):
        collector.add("securities.list_status_valid", "securities", "no listed securities found", severity="warning")


def check_trade_calendar(records: list[dict[str, Any]], collector: IssueCollector, start_date: str | None = None, end_date: str | None = None) -> set[str]:
    duplicate_key_issues("trade_calendar", records, ["cal_date"], collector, "trade_calendar.cal_date_unique")
    open_dates: set[str] = set()
    for record in records:
        cal_date = str(record.get("cal_date") or record.get("trade_date") or "")
        if not is_valid_date(cal_date):
            collector.add("trade_calendar.coverage", "trade_calendar", "invalid cal_date", field="cal_date", sample=record)
        is_open = record.get("is_open")
        if is_open not in {True, False, 0, 1, "0", "1"}:
            collector.add("trade_calendar.is_open_valid", "trade_calendar", "invalid is_open value", key=cal_date, field="is_open", sample=record)
        if is_open in {True, 1, "1"}:
            open_dates.add(cal_date)
    if start_date and open_dates and min(open_dates) > start_date:
        collector.add("trade_calendar.coverage", "trade_calendar", "calendar starts after expected start date", key=start_date)
    if end_date and open_dates and max(open_dates) < end_date:
        collector.add("trade_calendar.coverage", "trade_calendar", "calendar ends before expected end date", key=end_date)
    return open_dates


def check_daily_bars(
    records: list[dict[str, Any]],
    collector: IssueCollector,
    *,
    open_dates: set[str],
    securities_by_code: dict[str, dict[str, Any]],
) -> set[tuple[str, str]]:
    duplicate_key_issues("daily_bars", records, ["ts_code", "trade_date"], collector, "daily_bars.primary_key_unique")
    keys: set[tuple[str, str]] = set()
    for record in records:
        ts_code = str(record.get("ts_code") or "")
        trade_date = str(record.get("trade_date") or "")
        keys.add((ts_code, trade_date))
        prices = {field: as_float(record.get(field)) for field in ["open", "high", "low", "close", "pre_close"]}
        if any(value is not None and value < 0 for value in prices.values()):
            collector.add("daily_bars.ohlc_non_negative", "daily_bars", "negative OHLC price", key=f"{ts_code}|{trade_date}", sample=record)
        open_price, high, low, close = prices["open"], prices["high"], prices["low"], prices["close"]
        if None not in {open_price, high, low, close} and (high < max(open_price, close, low) or low > min(open_price, close, high)):
            collector.add("daily_bars.ohlc_order", "daily_bars", "high/low do not bracket open and close", key=f"{ts_code}|{trade_date}", sample=record)
        vol = as_float(record.get("volume", record.get("vol")))
        amount = as_float(record.get("amount"))
        if vol is not None and amount is not None and ((vol == 0 and amount > 0) or (amount == 0 and vol > 0)):
            collector.add("daily_bars.volume_amount_consistent", "daily_bars", "volume/amount zero mismatch", key=f"{ts_code}|{trade_date}", sample=record)
        pct_chg = as_float(record.get("pct_chg"))
        if pct_chg is not None and prices["pre_close"] not in {None, 0.0} and close is not None:
            expected = (close / prices["pre_close"] - 1.0) * 100.0
            if abs(expected - pct_chg) > 0.5:
                collector.add("daily_bars.pct_chg_consistency", "daily_bars", "pct_chg differs from close/pre_close", key=f"{ts_code}|{trade_date}", sample=record)
        if open_dates and trade_date not in open_dates:
            collector.add("daily_bars.trade_date_open", "daily_bars", "daily bar on non-open trading day", key=f"{ts_code}|{trade_date}", sample=record)
        security = securities_by_code.get(ts_code)
        if security:
            list_date = str(security.get("list_date") or "")
            delist_date = str(security.get("delist_date") or "")
            if list_date and trade_date < list_date:
                collector.add("daily_bars.security_lifecycle", "daily_bars", "daily bar before list_date", key=f"{ts_code}|{trade_date}", sample=record)
            if delist_date and trade_date > delist_date:
                collector.add("daily_bars.security_lifecycle", "daily_bars", "daily bar after delist_date", key=f"{ts_code}|{trade_date}", sample=record)
    return keys


def check_daily_basic(records: list[dict[str, Any]], collector: IssueCollector) -> set[tuple[str, str]]:
    duplicate_key_issues("daily_basic", records, ["ts_code", "trade_date"], collector, "daily_basic.primary_key_unique")
    keys: set[tuple[str, str]] = set()
    for record in records:
        ts_code = str(record.get("ts_code") or "")
        trade_date = str(record.get("trade_date") or "")
        keys.add((ts_code, trade_date))
        for field in ["turnover_rate", "volume_ratio", "total_mv", "circ_mv"]:
            value = as_float(record.get(field))
            if value is not None and value < 0:
                collector.add("daily_basic.non_negative", "daily_basic", f"{field} is negative", key=f"{ts_code}|{trade_date}", field=field, sample=record)
        pb = as_float(record.get("pb"))
        if pb is not None and pb <= 0:
            collector.add("daily_basic.non_negative", "daily_basic", "pb is non-positive", key=f"{ts_code}|{trade_date}", field="pb", sample=record)
    return keys


def check_adjustment_factors(records: list[dict[str, Any]], collector: IssueCollector) -> None:
    duplicate_key_issues("adjustment_factors", records, ["ts_code", "trade_date"], collector, "adjustment_factors.primary_key_unique")
    for record in records:
        value = as_float(record.get("adj_factor"))
        if value is None or value <= 0:
            collector.add("adjustment_factors.positive", "adjustment_factors", "adj_factor must be positive", key=record_key(record, ["ts_code", "trade_date"]), field="adj_factor", sample=record)


def check_daily_limits(records: list[dict[str, Any]], collector: IssueCollector) -> dict[tuple[str, str], dict[str, Any]]:
    duplicate_key_issues("daily_limits", records, ["ts_code", "trade_date"], collector, "daily_limits.primary_key_unique")
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        key = (str(record.get("ts_code") or ""), str(record.get("trade_date") or ""))
        by_key[key] = record
        up_limit = as_float(record.get("up_limit"))
        down_limit = as_float(record.get("down_limit"))
        if (up_limit is not None and up_limit < 0) or (down_limit is not None and down_limit < 0) or (
            up_limit is not None and down_limit is not None and up_limit < down_limit
        ):
            collector.add("daily_limits.price_order", "daily_limits", "invalid up_limit/down_limit range", key="|".join(key), sample=record)
    return by_key


def check_index_members(records: list[dict[str, Any]], collector: IssueCollector, securities_by_code: dict[str, dict[str, Any]]) -> None:
    duplicate_key_issues("index_members", records, ["index_code", "trade_date", "ts_code"], collector, "index_members.primary_key_unique")
    weight_by_index_date: dict[tuple[str, str], float] = defaultdict(float)
    for record in records:
        index_code = str(record.get("index_code") or "")
        trade_date = str(record.get("trade_date") or record.get("weight_date") or "")
        ts_code = str(record.get("ts_code") or record.get("con_code") or "")
        weight = as_float(record.get("weight"))
        if weight is not None:
            weight_by_index_date[(index_code, trade_date)] += weight
            if weight < 0:
                collector.add("index_members.weight_non_negative", "index_members", "index member weight is negative", key=f"{index_code}|{trade_date}|{ts_code}", sample=record)
        if securities_by_code and ts_code and ts_code not in securities_by_code:
            collector.add("index_members.security_exists", "index_members", "index member ts_code not found in securities", key=ts_code, sample=record)
    for (index_code, trade_date), total_weight in weight_by_index_date.items():
        if total_weight and not (90.0 <= total_weight <= 110.0):
            collector.add("index_members.weight_non_negative", "index_members", "index weights do not sum near 100", key=f"{index_code}|{trade_date}", sample={"weight_sum": total_weight})


def check_corporate_actions(records: list[dict[str, Any]], collector: IssueCollector) -> None:
    for record in records:
        ts_code = str(record.get("ts_code") or "")
        for field in ["ann_date", "ex_date", "record_date", "pay_date"]:
            value = record.get(field)
            if value and not is_valid_date(value):
                collector.add("corporate_actions.date_fields", "corporate_actions", f"invalid {field}", key=ts_code, field=field, sample=record)
        if not record.get("ann_date"):
            collector.add("corporate_actions.date_fields", "corporate_actions", "missing ann_date", key=ts_code, field="ann_date", sample=record)
        for field in ["cash_div", "stk_div", "stock_div", "bonus_share"]:
            value = as_float(record.get(field))
            if value is not None and value < 0:
                collector.add("corporate_actions.non_negative", "corporate_actions", f"{field} is negative", key=ts_code, field=field, sample=record)


def check_financial_features(records: list[dict[str, Any]], collector: IssueCollector) -> None:
    duplicate_key_issues("financial_features", records, ["ts_code", "end_date", "ann_date"], collector, "financial_features.pit_ann_date")
    for record in records:
        key = record_key(record, ["ts_code", "end_date", "ann_date"])
        end_date = str(record.get("end_date") or "")
        ann_date = str(record.get("ann_date") or "")
        if not ann_date:
            collector.add("financial_features.pit_ann_date", "financial_features", "missing ann_date creates weak PIT", key=key, field="ann_date", sample=record)
        elif end_date and end_date > ann_date:
            collector.add("financial_features.pit_ann_date", "financial_features", "end_date is after ann_date", key=key, sample=record)
