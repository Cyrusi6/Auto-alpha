"""Dataclasses for local matrix cache artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class MatrixFieldInfo:
    name: str
    path: str
    shape: list[int]
    dtype: str


@dataclass(frozen=True)
class MatrixCacheBuildResult:
    cache_dir: str
    metadata_path: str
    fields_path: str
    ts_codes_path: str
    trade_dates_path: str
    fields: list[str]
    n_stocks: int
    n_dates: int
    cache_hash: str
    validation_report_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MatrixValidationReport:
    cache_dir: str
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    n_stocks: int = 0
    n_dates: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
