"""Calendar helpers for production replay."""

from __future__ import annotations

import json
from pathlib import Path


def build_replay_trade_dates(
    data_dir: str | Path,
    start_date: str,
    end_date: str,
    explicit_trade_dates: list[str] | None = None,
    strict: bool = False,
) -> list[str]:
    if explicit_trade_dates:
        dates = sorted({str(item) for item in explicit_trade_dates if str(item)})
        return [date for date in dates if start_date <= date <= end_date]
    calendar_path = Path(data_dir) / "trade_calendar" / "records.jsonl"
    dates: list[str] = []
    if calendar_path.exists():
        with calendar_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                date = str(record.get("trade_date") or "")
                if start_date <= date <= end_date and record.get("is_open") is True:
                    dates.append(date)
    if dates:
        return sorted(set(dates))
    if strict:
        raise ValueError(f"no open trade dates found in {calendar_path}")
    return _date_strings(start_date, end_date)


def _date_strings(start_date: str, end_date: str) -> list[str]:
    from datetime import datetime, timedelta

    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    dates: list[str] = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates
