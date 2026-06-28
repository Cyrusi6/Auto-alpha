"""Dataclasses for batch factor research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FactorCandidate:
    name: str
    formula_tokens: list[int]
    formula_names: list[str]
    description: str | None = None
    formula_hash: str | None = None
    complexity: int | None = None
    lookback: int | None = None
    source: str | None = None
    parent_hashes: list[str] | None = None
    generation: int | None = None
    validation_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BatchResearchConfig:
    data_dir: str
    universe_name: str | None
    universe_file: str | None
    factor_store_dir: str
    report_dir: str
    output_dir: str
    factor_transform: str = "raw"
    enable_gate: bool = True
    correlation_threshold: float = 0.95
    min_coverage: float = 0.8
    top_k: int = 5
    composite_method: str = "rank_average"
    train_ratio: float = 0.6
    valid_ratio: float = 0.2
    continue_on_error: bool = True
    disable_composite: bool = False
    batch_id: str | None = None
    search_id: str | None = None
    matrix_cache_dir: str | None = None
    use_matrix_cache: bool = False
    use_batch_eval: bool = False
    batch_eval_output_dir: str | None = None
    batch_eval_chunk_size: int = 32
    batch_eval_device: str = "auto"
    use_eval_cache: bool = False
    eval_cache_dir: str | None = None
    point_in_time: bool = False
    feature_cutoff_mode: str = "same_day_after_close"
    min_listing_days: int = 0
    exclude_st: bool = False
    run_leakage_audit: bool = False
    leakage_audit_dir: str | None = None
    fail_on_leakage_blocker: bool = False
    corporate_action_aware: bool = False
    target_return_mode: str = "adjusted_close"
    corporate_action_dir: str | None = None
    corporate_action_cash_field: str = "cash_div"
    data_freeze_dir: str | None = None
    data_freeze_id: str | None = None
    data_version_manifest_path: str | None = None
    require_data_freeze: bool = False
    freeze_validation_report_path: str | None = None
    compute_state_dir: str | None = None
    compute_output_dir: str | None = None
    use_compute_scheduler: bool = False
    formula_shard_count: int = 1
    formula_shard_id: int | None = None
    resource_report_path: str | None = None
    feature_set_name: str = "ashare_features_v1"
    feature_set_manifest_path: str | None = None
    alpha_campaign_id: str | None = None
    alpha_candidates_path: str | None = None
    alpha_factory_report_path: str | None = None


@dataclass(frozen=True)
class CandidateRunResult:
    candidate: FactorCandidate
    factor_id: str | None
    status: str
    metrics_by_split: dict[str, dict[str, float]]
    score: float
    gate_reasons: list[str]
    max_abs_correlation: float
    report_json_path: str | None = None
    report_md_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BatchResearchResult:
    batch_id: str
    created_at: str
    results: list[CandidateRunResult]
    approved_factor_ids: list[str]
    rejected_factor_ids: list[str]
    composite_factor_id: str | None
    paths: dict[str, str]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
