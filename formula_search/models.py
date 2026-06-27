"""Dataclasses for local formula search."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FormulaCandidate:
    formula_tokens: list[int]
    formula_names: list[str]
    formula_hash: str
    complexity: int
    lookback: int
    source: str
    parent_hashes: list[str]
    generation: int
    validation_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaSearchConfig:
    seed: int = 42
    population_size: int = 20
    generations: int = 3
    max_formula_len: int = 8
    max_complexity: int = 20
    max_lookback: int = 10
    mutation_rate: float = 0.7
    crossover_rate: float = 0.3
    elite_size: int = 5
    top_k: int = 5
    candidate_batch_size: int | None = None


@dataclass(frozen=True)
class FormulaSearchResult:
    search_id: str
    generations: list[dict[str, Any]]
    candidates_generated: int
    candidates_valid: int
    candidates_evaluated: int
    approved_factor_ids: list[str]
    composite_factor_id: str | None
    best_candidates: list[dict[str, Any]]
    paths: dict[str, str]
    config: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
