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
    build_formula_corpus: bool = False
    formula_corpus_dir: str | None = None
    pretrain_alphagpt: bool = False
    pretrain_dir: str | None = None
    pretrain_epochs: int = 1
    pretrain_batch_size: int = 8
    pretrain_max_sequences: int | None = None
    pretrain_device: str = "auto"
    pretrain_preference_steps: int = 0
    use_batch_eval: bool = False
    batch_eval_dir: str | None = None
    batch_eval_chunk_size: int = 32
    batch_eval_device: str = "auto"
    use_eval_cache: bool = False
    eval_cache_dir: str | None = None
    register_model_version: bool = False
    model_registry_dir: str | None = None
    create_model_review_package: bool = False
    model_lifecycle_output_dir: str | None = None
    require_model_approval: bool = False
    model_lifecycle_policy_path: str | None = None
    model_approval_store_dir: str | None = None
    point_in_time: bool = False
    feature_cutoff_mode: str = "same_day_after_close"
    min_listing_days: int = 0
    exclude_st: bool = False
    run_pit_validation: bool = False
    pit_output_dir: str | None = None
    run_leakage_audit: bool = False
    leakage_audit_dir: str | None = None
    fail_on_pit_blocker: bool = False
    fail_on_leakage_blocker: bool = False
    include_corporate_actions: bool = True
    corporate_action_output_dir: str | None = None
    run_corporate_action_report: bool = False
    corporate_action_aware: bool = False
    target_return_mode: str = "adjusted_close"
    corporate_action_dir: str | None = None
    corporate_action_cash_field: str = "cash_div"
    corporate_action_application_date_mode: str = "pay_date"
    reconcile_adjustment_factors: bool = False
    fail_on_corporate_action_error: bool = False
    settlement_aware: bool = False
    settlement_dir: str | None = None
    settlement_profile: str = "cn_ashare_paper_default"
    cost_basis_method: str = "average"
    data_freeze_dir: str | None = None
    data_freeze_id: str | None = None
    data_version_manifest_path: str | None = None
    freeze_validation_report_path: str | None = None
    require_data_freeze: bool = False
    create_data_version: bool = False
    data_lake_registry_dir: str | None = None
    create_research_freeze: bool = False
    research_freeze_dir: str | None = None
    freeze_mode: str = "copy"
    validate_data_freeze: bool = False
    fail_on_freeze_error: bool = False
    use_compute_scheduler: bool = False
    compute_state_dir: str | None = None
    compute_output_dir: str | None = None
    experiment_output_dir: str | None = None
    experiment_workflow: str = "full_research_compute_smoke"
    gpu_count: int = 0
    shard_count: int = 1
    formula_shards: int = 1
    use_ddp_pretrain: bool = False
    pretrain_world_size: int = 1
    max_parallel_gpu_jobs: int = 1
    max_parallel_cpu_jobs: int = 1
    resource_report_dir: str | None = None
    resume_compute: bool = False
    compute_dry_run: bool = False
    run_alpha_factory: bool = False
    alpha_factory_dir: str | None = None
    alpha_campaign_name: str = "suite_alpha_campaign"
    alpha_candidate_budget: int = 36
    alpha_template_budget: int = 10
    alpha_random_budget: int = 10
    alpha_mutation_budget: int = 8
    alpha_crossover_budget: int = 4
    alpha_corpus_budget: int = 8
    alpha_neural_budget: int = 0
    alpha_feature_set_name: str = "ashare_features_v1"
    alpha_build_feature_set: bool = False
    alpha_feature_output_dir: str | None = None
    alpha_use_batch_eval: bool = False
    alpha_use_compute_scheduler: bool = False
    alpha_shard_count: int = 1
    alpha_top_k: int = 8
    alpha_max_per_family: int = 3
    alpha_min_novelty_score: float = 0.0
    alpha_register_shortlist: bool = False
    use_alpha_shortlist_for_search: bool = False
    run_validation_lab: bool = False
    validation_lab_dir: str | None = None
    validation_split_method: str = "simple_walk_forward"
    validation_train_size: int = 1
    validation_size: int = 0
    validation_test_size: int = 1
    validation_step_size: int = 1
    validation_embargo_size: int = 0
    validation_cscv_groups: int = 2
    validation_max_cscv_combinations: int = 6
    run_multiple_testing: bool = False
    run_overfit_risk: bool = False
    run_placebo: bool = False
    placebo_trials: int = 12
    run_regime_validation: bool = False
    run_sensitivity_validation: bool = False
    run_stress_backtest_validation: bool = False
    run_validation_campaign_store: bool = False
    validation_campaign_store_dir: str | None = None
    validation_candidate_pool_path: str | None = None
    validation_campaign_shard_count: int = 1
    validation_campaign_max_candidates: int = 0
    validation_campaign_top_k_certification: int = 20
    write_factor_certification_queue: bool = False
    run_factor_certification_campaign_store: bool = False
    factor_certification_campaign_dir: str | None = None
    factor_certification_queue_path: str | None = None
    factor_certification_campaign_max_items: int = 0
    write_certified_factor_pool: bool = False
    run_factor_certification: bool = False
    factor_certification_dir: str | None = None
    certification_policy_path: str | None = None
    certification_policy_profile: str = "sample_lenient_certification"
    require_certification: bool = False
    fail_on_certification_rejected: bool = False
    run_portfolio_lab: bool = False
    portfolio_lab_dir: str | None = None
    portfolio_lab_scenario_profile: str = "sample"
    portfolio_policy_grid_path: str | None = None
    portfolio_methods: str = "equal_weight,risk_aware"
    portfolio_risk_aversions: str = "0.5,1.0"
    portfolio_turnover_penalties: str = "0.0,0.1"
    portfolio_benchmark_weights: str = "1.0"
    portfolio_max_weight_values: str = "0.10"
    portfolio_max_names_values: str = "2,20"
    portfolio_max_turnover_values: str = "1.0"
    portfolio_max_tracking_error_values: str = "1.0"
    portfolio_top_n_values: str = "2,20"
    run_portfolio_certification: bool = False
    portfolio_certification_dir: str | None = None
    portfolio_certification_policy_path: str | None = None
    portfolio_certification_policy_profile: str = "sample_lenient_portfolio"
    require_portfolio_certification: bool = False
    fail_on_portfolio_certification_rejected: bool = False
    run_portfolio_campaign_store: bool = False
    portfolio_campaign_dir: str | None = None
    certified_factor_pool_path: str | None = None
    portfolio_campaign_max_items: int = 0
    write_production_candidate_bundle: bool = False
    write_optimizer_policy_activation_queue: bool = False
    create_portfolio_policy_approval: bool = False
    portfolio_policy_approval_store_dir: str | None = None
    register_optimizer_policy: bool = False

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
    certification_status: str | None = None
    require_certification: bool = False


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
