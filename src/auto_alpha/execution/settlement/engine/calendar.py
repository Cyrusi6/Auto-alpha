"""Trading-day based settlement calendar helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import SettlementProfile


class SettlementCalendar:
    def __init__(self, trade_dates: Iterable[str], warnings: list[str] | None = None):
        self.trade_dates = sorted({str(date) for date in trade_dates if date})
        self.warnings = warnings or []

    @classmethod
    def from_data_dir(cls, data_dir: str | Path) -> "SettlementCalendar":
        path = Path(data_dir) / "trade_calendar" / "records.jsonl"
        warnings: list[str] = []
        dates: list[str] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if bool(record.get("is_open", True)):
                    dates.append(str(record.get("trade_date") or ""))
        else:
            warnings.append("trade_calendar_missing_fallback_to_observed_dates")
            for dataset in ("daily_bars", "daily_basic", "daily_limits", "adjustment_factors"):
                dataset_path = Path(data_dir) / dataset / "records.jsonl"
                if not dataset_path.exists():
                    continue
                for line in dataset_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        dates.append(str(json.loads(line).get("trade_date") or ""))
        return cls(dates, warnings=warnings)

    def next_trade_date(self, date: str, offset: int = 1) -> str:
        if not self.trade_dates:
            return str(date)
        date = str(date)
        if date in self.trade_dates:
            idx = self.trade_dates.index(date)
        else:
            idx = 0
            for i, trade_date in enumerate(self.trade_dates):
                if trade_date >= date:
                    idx = i
                    break
            else:
                idx = len(self.trade_dates) - 1
        target = max(0, min(idx + int(offset), len(self.trade_dates) - 1))
        return self.trade_dates[target]


def load_settlement_profile(name: str = "cn_ashare_paper_default", **overrides) -> SettlementProfile:
    name = name or "cn_ashare_paper_default"
    if name == "immediate_legacy":
        profile = SettlementProfile(
            profile_name=name,
            buy_cash_settlement_lag_days=0,
            sell_cash_usable_lag_days=0,
            sell_cash_withdrawable_lag_days=0,
            buy_share_available_lag_days=0,
            sell_share_delivery_lag_days=0,
            allow_same_day_sell_proceeds_for_buy=True,
            allow_unsettled_cash_for_buy=True,
            allow_unsettled_shares_for_sell=True,
        )
    elif name == "conservative_t_plus_one_cash":
        profile = SettlementProfile(
            profile_name=name,
            buy_cash_settlement_lag_days=0,
            sell_cash_usable_lag_days=1,
            sell_cash_withdrawable_lag_days=2,
            buy_share_available_lag_days=1,
            sell_share_delivery_lag_days=0,
        )
    else:
        profile = SettlementProfile(profile_name="cn_ashare_paper_default")
    payload = profile.to_dict()
    payload.update({key: value for key, value in overrides.items() if value is not None})
    return SettlementProfile(**payload)
