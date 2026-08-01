"""Production calendar helpers backed by local A-share artifacts."""

from __future__ import annotations

import json
from pathlib import Path


class ProductionCalendar:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.trade_dates, self.warning = self._load_trade_dates()

    def is_trade_date(self, date: str) -> bool:
        return date in self.trade_dates

    def previous_trade_date(self, date: str) -> str | None:
        previous = [item for item in self.trade_dates if item < date]
        return previous[-1] if previous else None

    def next_trade_date(self, date: str) -> str | None:
        future = [item for item in self.trade_dates if item > date]
        return future[0] if future else None

    def context(self, trade_date: str) -> dict[str, object]:
        return {
            "trade_date": trade_date,
            "is_trade_date": self.is_trade_date(trade_date),
            "previous_trade_date": self.previous_trade_date(trade_date),
            "next_trade_date": self.next_trade_date(trade_date),
            "calendar_warning": self.warning,
            "trade_date_count": len(self.trade_dates),
        }

    def _load_trade_dates(self) -> tuple[list[str], str]:
        calendar_path = self.data_dir / "trade_calendar" / "records.jsonl"
        dates: list[str] = []
        if calendar_path.exists():
            for line in calendar_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("is_open", True):
                    dates.append(str(row.get("trade_date") or row.get("cal_date") or ""))
            return sorted({item for item in dates if item}), ""
        bars_path = self.data_dir / "daily_bars" / "records.jsonl"
        if bars_path.exists():
            for line in bars_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    dates.append(str(json.loads(line).get("trade_date") or ""))
            return sorted({item for item in dates if item}), "missing trade_calendar; fallback to daily_bars dates"
        return [], "missing trade_calendar and daily_bars"


def production_date_context(data_dir: str | Path, trade_date: str) -> dict[str, object]:
    return ProductionCalendar(data_dir).context(trade_date)
