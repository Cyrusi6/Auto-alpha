"""Dataclasses for validation lab artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ValidationSeverity:
    info = "info"
    warning = "warning"
    error = "error"
    blocker = "blocker"


class ValidationSplitMethod:
    simple_walk_forward = "simple_walk_forward"
    rolling_walk_forward = "rolling_walk_forward"
    anchored_walk_forward = "anchored_walk_forward"
    purged_embargo = "purged_embargo"
    cscv = "cscv"
    time_block_bootstrap = "time_block_bootstrap"


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationSplit:
    split_id: str
    method: str
    train_dates: list[str]
    validation_dates: list[str]
    test_dates: list[str]
    embargo_dates: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactorValidationTarget:
    factor_id: str
    factor_type: str
    formula_hash: str | None = None
    formula_names: list[str] = field(default_factory=list)
    feature_set_name: str | None = None
    alpha_campaign_id: str | None = None
    source_artifacts: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactorValidationWindowResult:
    split_id: str
    method: str
    train_metrics: dict[str, float]
    validation_metrics: dict[str, float]
    test_metrics: dict[str, float]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactorValidationSummary:
    factor_id: str
    split_method: str
    split_count: int
    out_of_sample_score: float
    cost_adjusted_score: float
    capacity_adjusted_score: float
    risk_adjusted_score: float
    window_pass_ratio: float
    stability_score: float
    mean_rank_ic: float
    mean_icir: float
    train_test_decay: float
    max_single_window_loss: float
    blocker_count: int
    warning_count: int
    status: str
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MultipleTestingSummary:
    total_trials: int
    valid_trials: int
    evaluated_trials: int
    selected_trials: int
    unique_formula_hash_count: int
    unique_feature_adjusted_formula_count: int
    source_trial_distribution: dict[str, int]
    effective_trial_count: int
    best_score: float
    median_score: float
    score_zscore: float
    multiple_testing_penalty: float
    selection_bias_warning: bool
    approximate: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OverfitRiskSummary:
    pbo_estimate: float
    cscv_logit_values: list[float]
    in_sample_rank: float
    out_sample_rank: float
    degradation_ratio: float
    overfit_risk_level: str
    deflated_sharpe_like_score: float
    deflated_ic_like_score: float
    selected_candidate_rank_stability: float
    insufficient_data: bool = False
    approximate: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlaceboTestResult:
    factor_id: str
    n_trials: int
    candidate_score: float
    placebo_score_distribution: list[float]
    candidate_vs_placebo_percentile: float
    placebo_passed: bool
    null_exceedance_count: int
    null_exceedance_ratio: float
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RegimeValidationResult:
    regime_name: str
    dates: list[str]
    metrics: dict[str, float]
    passed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SensitivityTestResult:
    scenario_id: str
    parameters: dict[str, Any]
    metrics: dict[str, float]
    passed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StressBacktestResult:
    scenario_id: str
    parameters: dict[str, Any]
    metrics: dict[str, float]
    passed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationLabReport:
    created_at: str
    target: dict[str, Any]
    split_method: str
    splits: list[dict[str, Any]]
    validation_summary: dict[str, Any]
    multiple_testing_summary: dict[str, Any]
    overfit_risk_summary: dict[str, Any]
    placebo_summary: dict[str, Any]
    regime_summary: dict[str, Any]
    sensitivity_summary: dict[str, Any]
    stress_backtest_summary: dict[str, Any]
    issues: list[dict[str, Any]]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
