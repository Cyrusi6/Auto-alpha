"""Evaluation utilities for A-share factor experiments."""

from auto_alpha.research.discovery.evaluation_metrics import evaluate_by_date_mask
from auto_alpha.research.discovery.evaluation_metrics import evaluate_by_splits
from auto_alpha.research.discovery.evaluation_multi_objective import ObjectiveSpec
from auto_alpha.research.discovery.evaluation_multi_objective import bounded_factor_score
from auto_alpha.research.discovery.evaluation_multi_objective import normalize_objective_rows
from auto_alpha.research.discovery.evaluation_report import FactorReport
from auto_alpha.research.discovery.evaluation_report import build_factor_report
from auto_alpha.research.discovery.evaluation_report import write_factor_report
from auto_alpha.research.discovery.evaluation_split import TimeSeriesSplitResult
from auto_alpha.research.discovery.evaluation_split import split_trade_dates

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
