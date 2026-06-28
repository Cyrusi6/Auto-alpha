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
