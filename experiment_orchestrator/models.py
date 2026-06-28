"""Dataclasses for experiment orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ExperimentStage:
    DATA_FREEZE_VALIDATE = "data_freeze_validate"
    MATRIX_BUILD = "matrix_build"
    FORMULA_CORPUS = "formula_corpus"
    FORMULA_CORPUS_SHARD = "formula_corpus_shard"
    FORMULA_BATCH_EVAL_SHARD = "formula_batch_eval_shard"
    FORMULA_BATCH_EVAL_MERGE = "formula_batch_eval_merge"
    ALPHAGPT_PRETRAIN = "alphagpt_pretrain"
    FORMULA_SEARCH_SHARD = "formula_search_shard"
    FORMULA_SEARCH_MERGE = "formula_search_merge"
    WALK_FORWARD_BACKTEST_SHARD = "walk_forward_backtest_shard"
    RESEARCH_SUITE = "research_suite"
    BENCHMARK = "benchmark"
    ARTIFACT_VALIDATION = "artifact_validation"


@dataclass(frozen=True)
class ExperimentShard:
    shard_id: int
    shard_count: int
    stage: str
    input_path: str | None
    output_dir: str
    shard_hash: str
    record_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExperimentGraphNode:
    node_id: str
    stage: str
    job_id: str | None = None
    shard_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExperimentGraphEdge:
    source: str
    target: str
    edge_type: str = "depends_on"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExperimentPlan:
    experiment_id: str
    workflow: str
    created_at: str
    output_dir: str
    shards: list[ExperimentShard]
    graph_nodes: list[ExperimentGraphNode]
    graph_edges: list[ExperimentGraphEdge]
    compute_jobs: list[dict[str, Any]]
    resource_plan: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["shards"] = [shard.to_dict() for shard in self.shards]
        payload["graph_nodes"] = [node.to_dict() for node in self.graph_nodes]
        payload["graph_edges"] = [edge.to_dict() for edge in self.graph_edges]
        return payload


@dataclass(frozen=True)
class ExperimentRunReport:
    experiment_id: str
    workflow: str
    status: str
    plan_path: str
    compute_run_report_path: str | None
    merge_report_path: str | None
    shard_count: int
    failed_shard_count: int
    summary: dict[str, Any]
    paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExperimentMergeReport:
    status: str
    shard_count: int
    merged_records: int
    duplicate_formula_hash_count: int
    missing_shard_count: int
    warnings: list[str]
    paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
