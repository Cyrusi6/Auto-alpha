"""Dataclasses for local benchmark reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BenchmarkItemResult:
    name: str
    wall_time_seconds: float
    n_stocks: int = 0
    n_dates: int = 0
    n_features: int = 0
    records_read: int = 0
    formulas_evaluated: int = 0
    throughput_estimate: float = 0.0
    success: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkResult:
    data_dir: str
    matrix_cache_dir: str | None
    output_dir: str
    items: list[BenchmarkItemResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
