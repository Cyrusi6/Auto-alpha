"""Evaluation utilities for A-share factor experiments."""

from .metrics import evaluate_by_date_mask, evaluate_by_splits
from .report import FactorReport, build_factor_report, write_factor_report
from .split import TimeSeriesSplitResult, split_trade_dates

__all__ = [
    "FactorReport",
    "TimeSeriesSplitResult",
    "build_factor_report",
    "evaluate_by_date_mask",
    "evaluate_by_splits",
    "split_trade_dates",
    "write_factor_report",
]
