"""Time-series date splitting helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimeSeriesSplitResult:
    train_dates: list[str]
    valid_dates: list[str]
    test_dates: list[str]
    embargo_dates: list[str]


def split_trade_dates(
    trade_dates: list[str],
    train_ratio: float = 0.6,
    valid_ratio: float = 0.2,
    embargo_size: int = 0,
) -> TimeSeriesSplitResult:
    dates = sorted(trade_dates)
    n_dates = len(dates)
    if n_dates == 0:
        return TimeSeriesSplitResult(train_dates=[], valid_dates=[], test_dates=[], embargo_dates=[])
    if n_dates == 1:
        return TimeSeriesSplitResult(train_dates=[], valid_dates=[], test_dates=dates, embargo_dates=[])
    if n_dates == 2:
        return TimeSeriesSplitResult(train_dates=dates[:1], valid_dates=[], test_dates=dates[1:], embargo_dates=[])

    train_count = max(1, int(n_dates * train_ratio))
    train_count = min(train_count, n_dates - 2)
    remaining = n_dates - train_count
    valid_count = max(1, int(n_dates * valid_ratio))
    valid_count = min(valid_count, remaining - 1)

    train_end = train_count
    valid_end = train_count + valid_count
    embargo = max(0, int(embargo_size))
    valid_start = min(train_end + embargo, valid_end)
    test_start = min(valid_end + embargo, n_dates)
    return TimeSeriesSplitResult(
        train_dates=dates[:train_end],
        valid_dates=dates[valid_start:valid_end],
        test_dates=dates[test_start:],
        embargo_dates=dates[train_end:valid_start] + dates[valid_end:test_start],
    )
