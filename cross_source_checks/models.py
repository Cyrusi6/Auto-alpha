"""Dataclasses for cross-source comparison reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class CrossSourceDatasetDiff:
    dataset: str
    left_records: int
    right_records: int
    record_count_diff: int
    missing_keys_left: int
    missing_keys_right: int
    numeric_field_max_abs_diff: float
    numeric_field_mean_abs_diff: float
    date_range_diff: bool
    left_date_range: list[str] = field(default_factory=list)
    right_date_range: list[str] = field(default_factory=list)
    ts_code_count_diff: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CrossSourceReport:
    left_data_dir: str
    right_data_dir: str
    datasets: list[CrossSourceDatasetDiff]
    has_differences: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
