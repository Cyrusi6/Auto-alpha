"""Dataclasses for reusable formula corpora."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FormulaCorpusConfig:
    factor_store_dir: str | None = None
    output_dir: str = "artifacts/formula_corpus"
    artifact_dirs: list[str] = field(default_factory=list)
    artifact_catalog_paths: list[str] = field(default_factory=list)
    include_defaults: bool = True
    include_seed: bool = True
    include_factor_store: bool = True
    max_records: int | None = None
    train_ratio: float = 0.8
    valid_ratio: float = 0.1
    preference_min_score_gap: float = 0.0
    max_preference_pairs: int = 1000
    seed: int = 42

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaCorpusRecord:
    formula_hash: str
    formula_tokens: list[int]
    formula_names: list[str]
    canonical_formula: list[str]
    valid: bool
    validation_reason: str
    complexity: int
    lookback: int
    status: str = "candidate"
    score: float = 0.0
    sources: list[str] = field(default_factory=list)
    factor_ids: list[str] = field(default_factory=list)
    metrics: dict[str, float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaSequenceRecord:
    formula_hash: str
    split: str
    prefix_tokens: list[int]
    target_token: int
    position: int
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaPreferencePair:
    pair_id: str
    split: str
    preferred_hash: str
    rejected_hash: str
    preferred_tokens: list[int]
    rejected_tokens: list[int]
    preferred_score: float
    rejected_score: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaCorpusStats:
    total_records: int
    valid_records: int
    invalid_records: int
    sequence_records: int
    preference_pairs: int
    status_counts: dict[str, int]
    source_counts: dict[str, int]
    max_complexity: int
    max_lookback: int
    avg_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaCorpusBuildResult:
    created_at: str
    config: dict[str, Any]
    stats: dict[str, Any]
    paths: dict[str, str]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
