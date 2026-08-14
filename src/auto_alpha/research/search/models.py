"""Research campaign models, templates, and immutable policy contracts."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any


class AlphaCampaignStatus:
    planned = "planned"
    running = "running"
    success = "success"
    failed = "failed"
    partial = "partial"
    cancelled = "cancelled"
    blocked = "blocked"


class AlphaCandidateSource:
    seed = "seed"
    default_candidates = "default_candidates"
    formula_corpus = "formula_corpus"
    template = "template"
    random = "random"
    mutation = "mutation"
    crossover = "crossover"
    neural_sampler = "neural_sampler"
    imported = "imported"


@dataclass(frozen=True)
class AlphaCandidateRecord:
    alpha_candidate_id: str
    formula_hash: str
    formula_tokens: list[int]
    formula_names: list[str]
    source: str
    source_refs: list[str]
    feature_set_name: str
    feature_version: str
    operator_version: str
    complexity: int
    lookback: int
    family_tags: list[str]
    validation_status: str = "unknown"
    static_check_status: str = "unknown"
    proxy_score: float = 0.0
    full_eval_score: float = 0.0
    novelty_score: float = 0.0
    diversity_group: str = ""
    final_score: float = 0.0
    status: str = "generated"
    reject_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaCampaignConfig:
    campaign_name: str
    data_dir: str
    output_dir: str
    factor_store_dir: str
    report_dir: str | None = None
    data_freeze_dir: str | None = None
    data_admission_verdict_path: str | None = None
    data_version_manifest_path: str | None = None
    require_data_freeze: bool = False
    formula_corpus_path: str | None = None
    candidates_json: str | None = None
    matrix_cache_dir: str | None = None
    device: str = "auto"
    universe_name: str | None = None
    universe_file: str | None = None
    feature_set_name: str = "ashare_features_v1"
    feature_set_manifest_path: str | None = None
    require_feature_family_ready: str | None = None
    exclude_weak_pit_features: bool = True
    feature_family_budget: str | None = None
    feature_promotion_policy_path: str | None = None
    feature_promotion_allowlist_path: str | None = None
    feature_promotion_denylist_path: str | None = None
    require_feature_promotion: bool = False
    allow_risk_filter_features: bool = False
    build_feature_set: bool = False
    feature_output_dir: str | None = None
    factor_transform: str = "raw"
    candidate_budget: int = 40
    template_budget: int = 12
    random_budget: int = 12
    mutation_budget: int = 8
    crossover_budget: int = 4
    corpus_budget: int = 8
    neural_budget: int = 0
    max_formula_len: int = 8
    max_complexity: int = 20
    max_lookback: int = 20
    proxy_max_candidates: int = 30
    proxy_max_dates: int = 63
    full_eval_max_candidates: int = 30
    top_k: int = 8
    max_per_family: int = 3
    min_novelty_score: float = 0.0
    max_pairwise_correlation: float = 0.99
    enable_gate: bool = True
    correlation_threshold: float = 0.99
    min_coverage: float = 0.5
    use_batch_eval: bool = False
    batch_eval_dir: str | None = None
    batch_eval_chunk_size: int = 8
    batch_eval_device: str = "auto"
    use_eval_cache: bool = False
    eval_cache_dir: str | None = None
    use_compute_scheduler: bool = False
    compute_state_dir: str | None = None
    compute_output_dir: str | None = None
    shard_count: int = 1
    max_parallel_gpu_jobs: int = 1
    max_parallel_cpu_jobs: int = 1
    point_in_time: bool = False
    feature_cutoff_mode: str = "same_day_after_close"
    corporate_action_aware: bool = False
    target_return_mode: str = "adjusted_close"
    settlement_aware: bool = False
    run_leakage_audit: bool = False
    leakage_audit_dir: str | None = None
    register_shortlist: bool = False
    refresh_candidates: bool = False
    refresh_proxy: bool = False
    refresh_eval: bool = False
    resume: bool = False
    seed: int = 42
    research_readiness_decision_path: str | None = None
    require_alpha_factory_ready: bool = False
    alpha_experiment_store_dir: str | None = None
    experiment_id: str | None = None
    register_experiment: bool = False
    consolidate_shards: bool = False
    consolidated_factor_store_dir: str | None = None
    write_leaderboard: bool = False
    validation_candidate_pool_dir: str | None = None
    max_validation_candidates: int = 50
    leaderboard_top_k: int = 100
    dedupe_across_campaigns: bool = False
    research_end_date: str | None = None
    holdout_start_date: str | None = None
    label_horizon: int = 2
    previous_experiment_dirs: list[str] = field(default_factory=list)
    provider: str = "sample"
    production_research: bool = False
    canonical_feature_tensor_path: str | None = None
    canonical_feature_validity_tensor_path: str | None = None
    canonical_research_view_manifest_path: str | None = None
    research_policy_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaCampaignManifest:
    campaign_id: str
    campaign_name: str
    data_freeze_id: str | None
    data_freeze_hash: str | None
    feature_set_name: str
    feature_set_version: str
    feature_version: str
    operator_version: str
    formula_corpus_hash: str | None
    generator_budgets: dict[str, int]
    random_seed: int
    compute_config: dict[str, Any]
    config_snapshot: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaFactoryReport:
    campaign_id: str
    status: str
    summary: dict[str, Any]
    paths: dict[str, str]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

from auto_alpha.research.formulas.semantics import FORMULA_VOCAB
from auto_alpha.research.features.factory import FEATURE_SET_V3, make_formula_vocab_from_manifest


def template_formulas(
    feature_set_name: str = "ashare_features_v1",
    feature_set_manifest=None,
    *,
    exclude_weak_pit_features: bool = True,
    required_feature_families: set[str] | None = None,
    feature_family_budget: dict[str, int] | None = None,
) -> list[dict[str, object]]:
    vocab = make_formula_vocab_from_manifest(feature_set_manifest) if feature_set_manifest is not None else FORMULA_VOCAB
    feature_meta = _feature_meta(feature_set_manifest)
    specs = [
        ("reversal_template", ["RET_1D"], ["reversal", "price_return"]),
        ("momentum_template", ["RET_5D"], ["momentum", "price_return"]),
        ("volatility_template", ["AMPLITUDE"], ["volatility"]),
        ("liquidity_template", ["LOG_AMOUNT"], ["liquidity"]),
        ("valuation_template", ["PB"], ["valuation"]),
        ("quality_growth_template", ["ROE", "REVENUE_YOY", "ADD"], ["quality", "growth"]),
        ("size_neutral_template", ["LOG_MKT_CAP"], ["size"]),
        ("price_volume_interaction_template", ["RET_1D", "TURNOVER_RATE", "MUL"], ["price_return", "liquidity"]),
        ("corporate_action_template", ["RET_5D"], ["corporate_action"]),
        ("index_membership_template", ["VOLUME_RATIO"], ["index_membership"]),
    ]
    if feature_set_name == FEATURE_SET_V3:
        v3_specs = [
            ("industry_relative_template", ["INDUSTRY_RELATIVE_RETURN_20D"], ["industry"]),
            ("moneyflow_reversal_template", ["MONEYFLOW_NET_RATIO", "RET_1D", "SUB"], ["moneyflow", "price_return"]),
            ("margin_crowding_template", ["MARGIN_CROWDING_Z20"], ["margin"]),
            ("financial_quality_template", ["ROA", "GROSS_MARGIN", "ADD"], ["financial_statement", "quality"]),
            ("cashflow_quality_template", ["OPERATING_CASHFLOW_TO_NET_INCOME", "FREE_CASHFLOW_PROXY", "ADD"], ["financial_statement", "cashflow"]),
            ("earnings_event_template", ["EXPRESS_SURPRISE_PROXY"], ["earnings_event"]),
            ("block_trade_discount_template", ["BLOCK_TRADE_DISCOUNT_PROXY"], ["abnormal_trading"]),
            ("holder_concentration_template", ["HOLDER_CONCENTRATION_PROXY"], ["holder_structure"]),
            ("pledge_risk_template", ["PLEDGE_RATIO"], ["pledge_repurchase_unlock"]),
            ("hk_holding_trend_template", ["HK_HOLDING_CHANGE_20D"], ["northbound"]),
        ]
        specs = v3_specs + specs
    result = []
    family_counts: dict[str, int] = {}
    for name, formula_names, tags in specs:
        if not _formula_allowed(formula_names, feature_meta, exclude_weak_pit_features):
            continue
        if required_feature_families and not (set(tags) & required_feature_families):
            continue
        if feature_family_budget and not _budget_available(tags, feature_family_budget, family_counts):
            continue
        try:
            tokens = [vocab.encode_name(item) for item in formula_names]
        except ValueError:
            continue
        result.append({"name": name, "formula_names": formula_names, "formula_tokens": tokens, "family_tags": tags})
        for tag in tags:
            family_counts[tag] = family_counts.get(tag, 0) + 1
    return result


def _feature_meta(manifest) -> dict[str, dict]:
    if manifest is None:
        return {}
    payload = manifest.to_dict() if hasattr(manifest, "to_dict") else dict(manifest)
    return {
        str(item.get("feature_name")): dict(item)
        for item in payload.get("feature_definitions", [])
        if isinstance(item, dict) and item.get("feature_name")
    }


def _formula_allowed(formula_names: list[str], meta: dict[str, dict], exclude_weak_pit: bool) -> bool:
    if not meta:
        return True
    for name in formula_names:
        if name not in meta:
            continue
        info = meta[name]
        if not info.get("default_enabled", True):
            return False
        if not info.get("used_for_alpha", True):
            return False
        if exclude_weak_pit and info.get("pit_safety") != "pit_safe":
            return False
    return True


def _budget_available(tags: list[str], budgets: dict[str, int], counts: dict[str, int]) -> bool:
    matching = [tag for tag in tags if tag in budgets]
    if not matching:
        return True
    return all(counts.get(tag, 0) < budgets[tag] for tag in matching)

import hashlib
import json


@dataclass(frozen=True)
class ObjectiveSpec:
    name: str
    direction: int
    weight: float
    required: bool = True

    def __post_init__(self) -> None:
        if self.direction not in {-1, 1}:
            raise ValueError(f"objective direction must be -1 or 1: {self.name}")
        if not math.isfinite(float(self.weight)) or float(self.weight) <= 0:
            raise ValueError(f"objective weight must be positive and finite: {self.name}")


@dataclass(frozen=True)
class AlphaResearchPolicy:
    policy_id: str = "alpha_factory_two_stage_oos_v1"
    proxy_neutralization: str = "neutralize_industry_size"
    proxy_min_coverage: float = 0.50
    proxy_min_cross_section_breadth: int = 30
    proxy_min_evaluable_dates: int = 20
    proxy_min_universe_count: int = 2
    proxy_max_abs_existing_correlation: float = 0.95
    train_size: int = 756
    validation_size: int = 126
    test_size: int = 126
    step_size: int = 126
    min_valid_oos_dates: int = 114
    min_evaluable_windows: int = 3
    min_cumulative_oos_dates: int = 342
    min_mean_rank_ic: float = 0.0
    min_mean_icir: float = 0.0
    min_window_pass_ratio: float = 0.50
    max_train_test_decay: float = 1.0
    placebo_trials: int = 40
    min_placebo_percentile: float = 0.80
    min_regime_pass_ratio: float = 0.50
    min_time_sensitivity_ratio: float = 0.50
    min_parameter_sensitivity_ratio: float = 0.67
    max_bh_q_value: float = 0.10
    max_selection_adjusted_p_value: float = 0.10
    max_pbo: float = 0.67
    max_abs_size_exposure: float = 0.50
    max_abs_beta_exposure: float = 0.50
    max_abs_liquidity_exposure: float = 0.50
    max_industry_concentration: float = 0.60
    modeled_cost_bps: float = 20.0
    capacity_participation: float = 0.10
    capacity_aum_cny: float = 1_000_000.0
    min_capacity_feasible_ratio: float = 0.90
    parameters_locked: bool = True
    certification_supported: bool = False
    proxy_objectives: tuple[ObjectiveSpec, ...] = field(
        default_factory=lambda: (
            ObjectiveSpec("neutralized_rank_ic_mean", 1, 2.0),
            ObjectiveSpec("ic_stability", 1, 1.0),
            ObjectiveSpec("coverage", 1, 1.0),
            ObjectiveSpec("turnover_proxy", -1, 0.75),
            ObjectiveSpec("complexity", -1, 0.50),
            ObjectiveSpec("lookback", -1, 0.50),
            ObjectiveSpec("max_abs_existing_correlation", -1, 1.0),
            ObjectiveSpec("family_novelty", 1, 0.75),
            ObjectiveSpec("universe_direction_consistency", 1, 1.0),
        )
    )
    full_objectives: tuple[ObjectiveSpec, ...] = field(
        default_factory=lambda: (
            ObjectiveSpec("mean_rank_ic", 1, 2.0),
            ObjectiveSpec("mean_icir", 1, 1.5),
            ObjectiveSpec("window_pass_ratio", 1, 1.0),
            ObjectiveSpec("stability_score", 1, 1.0),
            ObjectiveSpec("placebo_percentile", 1, 1.0),
            ObjectiveSpec("regime_pass_ratio", 1, 1.0),
            ObjectiveSpec("time_sensitivity_ratio", 1, 0.75),
            ObjectiveSpec("parameter_sensitivity_ratio", 1, 0.75),
            ObjectiveSpec("modeled_net_spread", 1, 0.75),
            ObjectiveSpec("capacity_feasible_ratio", 1, 0.75),
            ObjectiveSpec("max_style_exposure", -1, 0.75),
            ObjectiveSpec("pbo_estimate", -1, 1.0),
            ObjectiveSpec("bh_q_value", -1, 1.0),
        )
    )

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["proxy_objectives"] = [asdict(value) for value in self.proxy_objectives]
        payload["full_objectives"] = [asdict(value) for value in self.full_objectives]
        return payload

    @property
    def policy_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


POLICIES = {
    "alpha_factory_two_stage_oos_v1": AlphaResearchPolicy(),
    "alpha_factory_two_stage_smoke_v1": AlphaResearchPolicy(
        policy_id="alpha_factory_two_stage_smoke_v1",
        proxy_neutralization="raw",
        proxy_min_coverage=0.0,
        proxy_min_cross_section_breadth=2,
        proxy_min_evaluable_dates=1,
        proxy_min_universe_count=1,
        train_size=2,
        validation_size=1,
        test_size=1,
        step_size=1,
        min_valid_oos_dates=1,
        min_evaluable_windows=1,
        min_cumulative_oos_dates=1,
        placebo_trials=4,
        min_placebo_percentile=0.0,
        min_regime_pass_ratio=0.0,
        min_time_sensitivity_ratio=0.0,
        min_parameter_sensitivity_ratio=0.0,
        max_bh_q_value=1.0,
        max_selection_adjusted_p_value=1.0,
        max_pbo=1.0,
        max_abs_size_exposure=1.0,
        max_abs_beta_exposure=1.0,
        max_abs_liquidity_exposure=1.0,
        max_industry_concentration=1.0,
        min_capacity_feasible_ratio=0.0,
        parameters_locked=False,
    ),
}


def load_alpha_research_policy(policy_id: str | None, *, production_research: bool = False) -> AlphaResearchPolicy:
    key = policy_id or (
        "alpha_factory_two_stage_oos_v1"
        if production_research
        else "alpha_factory_two_stage_smoke_v1"
    )
    if key not in POLICIES:
        raise ValueError(f"unknown alpha research policy: {key}")
    if production_research and key != "alpha_factory_two_stage_oos_v1":
        raise ValueError("production research requires alpha_factory_two_stage_oos_v1")
    return POLICIES[key]
