"""Validation helpers for A-share data records."""

from __future__ import annotations

import re
from datetime import datetime

from .schema import DailyBar, FinancialFeature


_TS_CODE_PATTERN = re.compile(r"^\d{6}\.(SZ|SH|BJ)$")
_DATE_FORMAT = "%Y%m%d"


def is_valid_ts_code(ts_code: str) -> bool:
    if not isinstance(ts_code, str):
        return False
    return bool(_TS_CODE_PATTERN.fullmatch(ts_code))


def is_valid_yyyymmdd(value: str) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        return False
    try:
        parsed = datetime.strptime(value, _DATE_FORMAT)
    except ValueError:
        return False
    return parsed.strftime(_DATE_FORMAT) == value


def ensure_no_financial_lookahead(feature: FinancialFeature, trade_date: str) -> None:
    if not is_valid_yyyymmdd(trade_date):
        raise ValueError("trade_date must be a real date in YYYYMMDD format")
    if not is_valid_yyyymmdd(feature.announce_date):
        raise ValueError("announce_date must be a real date in YYYYMMDD format")
    if feature.announce_date > trade_date:
        raise ValueError(
            "financial feature is not available on trade_date: "
            f"announce_date={feature.announce_date}, trade_date={trade_date}"
        )


def validate_daily_bar(bar: DailyBar) -> None:
    if not is_valid_ts_code(bar.ts_code):
        raise ValueError(f"invalid ts_code: {bar.ts_code}")
    if not is_valid_yyyymmdd(bar.trade_date):
        raise ValueError(f"invalid trade_date: {bar.trade_date}")

    price_fields = {
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "pre_close": bar.pre_close,
    }
    for field_name, value in price_fields.items():
        if value < 0:
            raise ValueError(f"{field_name} must be non-negative")

    if bar.volume < 0:
        raise ValueError("volume must be non-negative")
    if bar.amount < 0:
        raise ValueError("amount must be non-negative")
    if bar.high < bar.low:
        raise ValueError("high must be greater than or equal to low")
