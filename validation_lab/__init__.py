"""Out-of-sample validation and anti-overfit diagnostics."""

from .models import (
    FactorValidationSummary,
    FactorValidationTarget,
    FactorValidationWindowResult,
    MultipleTestingSummary,
    OverfitRiskSummary,
    PlaceboTestResult,
    RegimeValidationResult,
    SensitivityTestResult,
    StressBacktestResult,
    ValidationIssue,
    ValidationLabReport,
    ValidationSeverity,
    ValidationSplit,
    ValidationSplitMethod,
)
from .metrics import evaluate_factor_dates, evaluate_factor_splits, summarize_window_results
from .splits import (
    build_anchored_walk_forward_splits,
    build_cscv_splits,
    build_purged_embargo_splits,
    build_rolling_walk_forward_splits,
    build_simple_walk_forward_splits,
    build_time_block_bootstrap_splits,
)

__all__ = [
    "FactorValidationSummary",
    "FactorValidationTarget",
    "FactorValidationWindowResult",
    "MultipleTestingSummary",
    "OverfitRiskSummary",
    "PlaceboTestResult",
    "RegimeValidationResult",
    "SensitivityTestResult",
    "StressBacktestResult",
    "ValidationIssue",
    "ValidationLabReport",
    "ValidationSeverity",
    "ValidationSplit",
    "ValidationSplitMethod",
    "evaluate_factor_dates",
    "evaluate_factor_splits",
    "summarize_window_results",
    "build_anchored_walk_forward_splits",
    "build_cscv_splits",
    "build_purged_embargo_splits",
    "build_rolling_walk_forward_splits",
    "build_simple_walk_forward_splits",
    "build_time_block_bootstrap_splits",
]
