"""Dataclasses for Alpha Factory campaigns."""

from __future__ import annotations

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
    data_version_manifest_path: str | None = None
    require_data_freeze: bool = False
    formula_corpus_path: str | None = None
    candidates_json: str | None = None
    matrix_cache_dir: str | None = None
    universe_name: str | None = None
    universe_file: str | None = None
    feature_set_name: str = "ashare_features_v1"
    feature_set_manifest_path: str | None = None
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
    proxy_max_dates: int = 3
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
    previous_experiment_dirs: list[str] = field(default_factory=list)

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
