"""Dataclasses for one-click research suites."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SuiteStageResult:
    name: str
    status: str
    started_at: str
    finished_at: str
    output_paths: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchSuiteConfig:
    suite_name: str
    data_dir: str
    universe_name: str
    index_code: str
    factor_store_dir: str
    report_dir: str
    output_dir: str
    backtest_dir: str
    orders_dir: str
    provider: str = "sample"
    as_of_date: str = "20240104"
    factor_transform: str = "winsorize_zscore"
    search_seed: int = 42
    search_mode: str = "random"
    search_population_size: int = 12
    search_generations: int = 2
    search_max_candidates: int | None = None
    neural_warmup_steps: int = 1
    neural_policy_steps: int = 1
    neural_checkpoint: str | None = None
    hybrid_neural_ratio: float = 0.5
    top_k: int = 5
    composite_method: str = "rank_average"
    portfolio_method: str = "equal_weight"
    risk_aversion: float = 1.0
    turnover_penalty: float = 0.1
    max_turnover: float = 1.0
    max_industry_active_weight: float = 0.20
    max_tracking_error: float = 1.0
    use_factor_risk_model: bool = False
    risk_model_lookback: int | None = None
    risk_model_shrinkage: float = 0.1
    attribution: bool = False
    max_style_exposure: float | None = None
    max_active_style_exposure: float | None = None
    max_factor_risk_contribution: float | None = None
    promote_latest_composite: bool = False
    pretty: bool = False
    skip_data_sync: bool = False
    skip_universe: bool = False
    skip_orders: bool = False
    disable_promotion: bool = False
    walk_forward_train_size: int = 1
    walk_forward_test_size: int = 1
    walk_forward_step_size: int = 1
    build_matrix_cache: bool = False
    matrix_cache_dir: str | None = None
    use_matrix_cache: bool = False
    benchmark: bool = False
    benchmark_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactEntry:
    name: str
    path: str
    kind: str
    stage: str
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ArtifactCatalog:
    suite_name: str
    created_at: str
    entries: list[ArtifactEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WalkForwardWindow:
    train_dates: list[str]
    test_dates: list[str]


@dataclass(frozen=True)
class WalkForwardResult:
    factor_id: str
    windows: list[dict[str, Any]]
    summary: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromotionConfig:
    min_mean_test_score: float = -999.0
    min_positive_test_score_ratio: float = 0.0
    min_fill_rate: float = 0.0
    max_constraint_reject_rate: float = 1.0
    max_tracking_error: float = 1.0
    max_constraint_violations: float = 999.0
    max_active_style_exposure_abs: float = 999.0
    max_factor_risk_share: float = 1.0
    max_specific_risk_share: float = 1.0
    require_composite: bool = True


@dataclass(frozen=True)
class PromotionDecision:
    factor_id: str
    passed: bool
    new_status: str
    reasons: list[str]
    checks: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchSuiteResult:
    suite_name: str
    status: str
    started_at: str
    finished_at: str
    stages: list[SuiteStageResult]
    selected_factor_id: str | None
    promotion_decision: PromotionDecision | None
    paths: dict[str, str]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
