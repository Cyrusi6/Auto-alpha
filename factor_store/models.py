"""Dataclasses for factor store records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FactorRecord:
    factor_id: str
    formula: list[str]
    formula_tokens: list[int]
    formula_hash: str
    feature_version: str
    operator_version: str
    lookback_days: int
    created_at: str
    status: str = "candidate"
    description: str | None = None
    metrics: dict[str, float] | None = None
    transform_method: str | None = None
    gate_status: str | None = None
    gate_reasons: list[str] | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    factor_id: str
    data_dir: str
    output_dir: str
    train_dates: list[str]
    valid_dates: list[str]
    test_dates: list[str]
    metrics_by_split: dict[str, dict[str, float]]
    created_at: str
    notes: str | None = None


@dataclass(frozen=True)
class FactorValueRecord:
    factor_id: str
    trade_date: str
    ts_code: str
    value: float | None


@dataclass(frozen=True)
class StorageResult:
    path: str
    records: int
