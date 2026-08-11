"""Out-of-sample validation and anti-overfit diagnostics."""

from auto_alpha.validation.walk_forward.engine_models import FactorValidationSummary
from auto_alpha.validation.walk_forward.engine_models import FactorValidationTarget
from auto_alpha.validation.walk_forward.engine_models import FactorValidationWindowResult
from auto_alpha.validation.walk_forward.engine_models import MultipleTestingSummary
from auto_alpha.validation.walk_forward.engine_models import OverfitRiskSummary
from auto_alpha.validation.walk_forward.engine_models import PlaceboTestResult
from auto_alpha.validation.walk_forward.engine_models import RegimeValidationResult
from auto_alpha.validation.walk_forward.engine_models import SensitivityTestResult
from auto_alpha.validation.walk_forward.engine_models import StressBacktestResult
from auto_alpha.validation.walk_forward.engine_models import ValidationIssue
from auto_alpha.validation.walk_forward.engine_models import ValidationLabReport
from auto_alpha.validation.walk_forward.engine_models import ValidationSeverity
from auto_alpha.validation.walk_forward.engine_models import ValidationSplit
from auto_alpha.validation.walk_forward.engine_models import ValidationSplitMethod
from auto_alpha.validation.walk_forward.engine_metrics import evaluate_factor_dates
from auto_alpha.validation.walk_forward.engine_metrics import evaluate_factor_splits
from auto_alpha.validation.walk_forward.engine_metrics import summarize_window_results
from auto_alpha.validation.walk_forward.engine_splits import build_anchored_walk_forward_splits
from auto_alpha.validation.walk_forward.engine_splits import build_cscv_splits
from auto_alpha.validation.walk_forward.engine_splits import build_purged_embargo_splits
from auto_alpha.validation.walk_forward.engine_splits import build_rolling_walk_forward_splits
from auto_alpha.validation.walk_forward.engine_splits import build_simple_walk_forward_splits
from auto_alpha.validation.walk_forward.engine_splits import build_time_block_bootstrap_splits

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
