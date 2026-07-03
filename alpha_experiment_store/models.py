"""Dataclasses for local Alpha Factory experiment warehousing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AlphaExperimentRecord:
    experiment_id: str
    campaign_id: str
    campaign_name: str
    data_freeze_id: str | None = None
    data_freeze_hash: str | None = None
    feature_set_name: str | None = None
    feature_set_hash: str | None = None
    matrix_cache_id: str | None = None
    matrix_cache_hash: str | None = None
    candidate_budget: int = 0
    shard_count: int = 0
    compute_run_id: str | None = None
    status: str = "registered"
    created_at: str = ""
    source_paths: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaShardRecord:
    shard_id: str
    experiment_id: str
    shard_index: int
    shard_count: int
    formula_count: int = 0
    evaluated_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    error_count: int = 0
    factor_store_dir: str | None = None
    batch_eval_result_path: str | None = None
    eval_results_path: str | None = None
    compute_job_id: str | None = None
    status: str = "registered"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaConsolidatedFactorRecord:
    consolidated_factor_id: str
    factor_id: str
    formula_hash: str
    feature_version: str
    operator_version: str
    campaign_id: str
    shard_id: str
    source: str
    status: str
    score: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    coverage: float = 0.0
    correlation_cluster_id: str | None = None
    family_tags: list[str] = field(default_factory=list)
    novelty_score: float = 0.0
    diversity_group: str | None = None
    selected_for_validation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaLeaderboardRecord:
    rank: int
    factor_id: str
    formula_hash: str
    final_score: float
    score_components: dict[str, float]
    validation_ready: bool
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaStoreWriteResult:
    path: str
    records: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
