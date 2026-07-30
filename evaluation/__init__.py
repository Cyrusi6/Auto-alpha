"""Evaluation utilities for A-share factor experiments."""

from .metrics import evaluate_by_date_mask, evaluate_by_splits
from .multi_objective import ObjectiveSpec, bounded_factor_score, normalize_objective_rows
from .report import FactorReport, build_factor_report, write_factor_report
from .split import TimeSeriesSplitResult, split_trade_dates

__all__ = [
    "FactorReport",
    "ObjectiveSpec",
    "TimeSeriesSplitResult",
    "build_factor_report",
    "bounded_factor_score",
    "evaluate_by_date_mask",
    "evaluate_by_splits",
    "split_trade_dates",
    "normalize_objective_rows",
    "write_factor_report",
]
