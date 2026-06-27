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
