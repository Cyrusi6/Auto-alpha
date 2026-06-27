"""Time-series date splitting helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimeSeriesSplitResult:
    train_dates: list[str]
    valid_dates: list[str]
    test_dates: list[str]


def split_trade_dates(
    trade_dates: list[str],
    train_ratio: float = 0.6,
    valid_ratio: float = 0.2,
) -> TimeSeriesSplitResult:
    dates = sorted(trade_dates)
    n_dates = len(dates)
    if n_dates == 0:
        return TimeSeriesSplitResult(train_dates=[], valid_dates=[], test_dates=[])
    if n_dates == 1:
        return TimeSeriesSplitResult(train_dates=[], valid_dates=[], test_dates=dates)
    if n_dates == 2:
        return TimeSeriesSplitResult(train_dates=dates[:1], valid_dates=[], test_dates=dates[1:])

    train_count = max(1, int(n_dates * train_ratio))
    train_count = min(train_count, n_dates - 2)
    remaining = n_dates - train_count
    valid_count = max(1, int(n_dates * valid_ratio))
    valid_count = min(valid_count, remaining - 1)

    train_end = train_count
    valid_end = train_count + valid_count
    return TimeSeriesSplitResult(
        train_dates=dates[:train_end],
        valid_dates=dates[train_end:valid_end],
        test_dates=dates[valid_end:],
    )
