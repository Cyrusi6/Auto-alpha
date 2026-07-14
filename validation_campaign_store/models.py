"""Dataclasses for validation campaign warehousing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ValidationCampaignRecord:
    validation_campaign_id: str
    source_alpha_experiment_id: str | None
    source_candidate_pool_path: str
    data_freeze_id: str | None = None
    data_freeze_hash: str | None = None
    feature_set_name: str | None = None
    matrix_cache_path: str | None = None
    validation_policy_profile: str = "sample"
    split_method: str = "simple_walk_forward"
    candidate_count: int = 0
    shard_count: int = 1
    status: str = "registered"
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationCandidateRecord:
    validation_candidate_id: str
    factor_id: str
    formula_hash: str
    formula_names: list[str]
    alpha_rank: int
    alpha_score: float
    family_tags: list[str]
    source_campaign_id: str | None
    feature_version: str
    factor_store_dir: str
    factor_values_path: str
    status: str = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationShardRecord:
    shard_id: str
    validation_campaign_id: str
    shard_index: int
    shard_count: int
    candidate_count: int
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    output_dir: str = ""
    validation_lab_report_path: str | None = None
    status: str = "planned"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationCandidateResult:
    validation_candidate_id: str
    factor_id: str
    formula_hash: str
    validation_status: str
    out_of_sample_score: float = 0.0
    rank_ic_mean: float = 0.0
    rank_ic_hit_rate: float = 0.0
    icir: float = 0.0
    pbo_estimate: float = 0.0
    deflated_ic_score: float = 0.0
    placebo_percentile: float = 0.0
    null_exceedance_ratio: float = 0.0
    regime_pass_ratio: float = 0.0
    sensitivity_pass_ratio: float = 0.0
    stress_pass_ratio: float = 0.0
    turnover_mean: float = 0.0
    coverage_mean: float = 0.0
    max_drawdown: float | None = None
    validation_score: float = 0.0
    blocker_count: int = 0
    warning_count: int = 0
    selected_for_certification: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationLeaderboardRecord:
    rank: int
    validation_candidate_id: str
    factor_id: str
    formula_hash: str
    validation_score: float
    score_components: dict[str, float]
    certification_ready: bool
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactorCertificationQueueRecord:
    queue_id: str
    validation_candidate_id: str
    factor_id: str
    priority: int
    certification_policy_profile: str
    validation_result_path: str
    factor_store_dir: str
    status: str = "queued"
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
