"""Dataclasses for chunked formula batch evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FormulaEvalRequest:
    name: str
    formula_tokens: list[int]
    formula_names: list[str]
    formula_hash: str
    description: str | None = None
    source: str | None = None
    complexity: int | None = None
    lookback: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaEvalResult:
    request: FormulaEvalRequest
    factor_id: str | None
    status: str
    score: float
    metrics_by_split: dict[str, dict[str, float]]
    gate_reasons: list[str]
    max_abs_correlation: float
    cache_hit: bool = False
    elapsed_seconds: float = 0.0
    error: str | None = None
    report_json_path: str | None = None
    report_md_path: str | None = None
    feature_set_name: str = "ashare_features_v1"
    feature_version: str = "ashare_features_v1"
    campaign_id: str | None = None
    alpha_candidate_id: str | None = None
    family_tags: list[str] | None = None
    proxy_score: float | None = None
    final_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaBatchEvalConfig:
    data_dir: str
    factor_store_dir: str
    report_dir: str
    output_dir: str
    universe_name: str | None = None
    universe_file: str | None = None
    matrix_cache_dir: str | None = None
    use_matrix_cache: bool = False
    device: str = "auto"
    strict_device: bool = False
    factor_transform: str = "raw"
    enable_gate: bool = True
    correlation_threshold: float = 0.95
    min_coverage: float = 0.8
    train_ratio: float = 0.6
    valid_ratio: float = 0.2
    chunk_size: int = 32
    use_eval_cache: bool = False
    eval_cache_dir: str | None = None
    skip_existing: bool = True
    register_approved: bool = False
    batch_id: str | None = None
    continue_on_error: bool = True
    shard_id: int | None = None
    shard_count: int = 1
    resource_report_path: str | None = None
    feature_set_name: str = "ashare_features_v1"
    feature_set_manifest_path: str | None = None
    alpha_campaign_id: str | None = None
    feature_promotion_policy_hash: str | None = None
    research_end_date: str | None = None
    holdout_start_date: str | None = None
    label_horizon: int = 2
    eligible_date_hash: str | None = None
    canonical_feature_tensor_path: str | None = None
    canonical_feature_validity_tensor_path: str | None = None
    research_computation_identity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaEvalCacheManifest:
    cache_dir: str
    enabled: bool
    cache_hits: int
    cache_writes: int
    cache_records: int
    keys: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaBatchEvalBenchmark:
    formulas_requested: int
    formulas_evaluated: int
    elapsed_seconds: float
    formulas_per_second: float
    device: str
    matrix_cache_used: bool
    chunk_size: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaBatchEvalResult:
    batch_id: str
    created_at: str
    status: str
    results: list[FormulaEvalResult]
    summary: dict[str, Any]
    paths: dict[str, str]
    cache_manifest: dict[str, Any]
    benchmark: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
