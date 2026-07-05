"""Cross-dataset semantic quality checks."""

from __future__ import annotations

from typing import Any

from .rules import IssueCollector, as_float


def run_cross_dataset_checks(
    datasets: dict[str, list[dict[str, Any]]],
    collector: IssueCollector,
    *,
    open_dates: set[str],
    daily_bar_keys: set[tuple[str, str]],
    daily_basic_keys: set[tuple[str, str]],
    daily_limits_by_key: dict[tuple[str, str], dict[str, Any]],
    securities_by_code: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    mismatches = {
        "daily_basic_missing_for_bars": 0,
        "daily_bars_missing_for_basic": 0,
        "limit_violations": 0,
        "unknown_security_records": 0,
        "non_open_daily_records": 0,
    }
    for key in sorted(daily_bar_keys - daily_basic_keys)[:200]:
        mismatches["daily_basic_missing_for_bars"] += 1
        collector.add("cross.daily_basic_key_mismatch", "cross_dataset", "daily_bars key missing from daily_basic", key="|".join(key), sample={"ts_code": key[0], "trade_date": key[1]})
    for key in sorted(daily_basic_keys - daily_bar_keys)[:200]:
        mismatches["daily_bars_missing_for_basic"] += 1
        collector.add("cross.daily_basic_key_mismatch", "cross_dataset", "daily_basic key missing from daily_bars", key="|".join(key), sample={"ts_code": key[0], "trade_date": key[1]})
    for record in datasets.get("daily_bars", []):
        key = (str(record.get("ts_code") or ""), str(record.get("trade_date") or ""))
        close = as_float(record.get("close"))
        limit = daily_limits_by_key.get(key)
        if close is not None and limit:
            up_limit = as_float(limit.get("up_limit"))
            down_limit = as_float(limit.get("down_limit"))
            tolerance = max(abs(close) * 0.002, 0.01)
            if up_limit is not None and close > up_limit + tolerance:
                mismatches["limit_violations"] += 1
                collector.add("cross.daily_limit_violation", "cross_dataset", "close above up_limit", key="|".join(key), sample={"close": close, "up_limit": up_limit})
            if down_limit is not None and close < down_limit - tolerance:
                mismatches["limit_violations"] += 1
                collector.add("cross.daily_limit_violation", "cross_dataset", "close below down_limit", key="|".join(key), sample={"close": close, "down_limit": down_limit})
        if open_dates and key[1] and key[1] not in open_dates:
            mismatches["non_open_daily_records"] += 1
    for dataset, records in datasets.items():
        if dataset == "securities":
            continue
        for record in records:
            ts_code = str(record.get("ts_code") or record.get("con_code") or "")
            if ts_code and securities_by_code and ts_code not in securities_by_code:
                mismatches["unknown_security_records"] += 1
                collector.add("cross.unknown_security", "cross_dataset", "ts_code not found in securities", key=f"{dataset}|{ts_code}", sample={"dataset": dataset, "ts_code": ts_code})
    return {
        "status": "warning" if any(mismatches.values()) else "ok",
        "mismatches": mismatches,
        "daily_bar_keys": len(daily_bar_keys),
        "daily_basic_keys": len(daily_basic_keys),
        "open_trade_dates": len(open_dates),
    }
