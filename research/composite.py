"""Composite factor construction and registration."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import torch

from factor_engine.correlation import factor_correlation
from factor_store import FactorRecord, LocalFactorStore, make_factor_id, stable_formula_hash


FEATURE_VERSION = "ashare_features_v1"
OPERATOR_VERSION = "ashare_ops_v1"
COMPOSITE_METHODS = {"equal_weight", "score_weighted", "rank_average"}


def select_approved_factors(
    store: LocalFactorStore,
    max_factors: int,
    min_score: float | None = None,
    max_pairwise_corr: float = 0.8,
) -> list[str]:
    if max_factors <= 0:
        return []
    approved = [
        record
        for record in store.load_factors()
        if record.status == "approved" and (record.factor_type in {None, "single"})
    ]
    approved.sort(key=lambda record: _factor_score(record), reverse=True)
    selected: list[FactorRecord] = []
    selected_records = []
    for record in approved:
        score = _factor_score(record)
        if min_score is not None and score < min_score:
            continue
        records = store.load_factor_values(record.factor_id)
        if selected_records:
            max_corr = max(abs(_aligned_correlation(records, other)) for other in selected_records)
            if max_corr > max_pairwise_corr:
                continue
        selected.append(record)
        selected_records.append(records)
        if len(selected) >= max_factors:
            break
    return [record.factor_id for record in selected]


def build_composite_factor_matrix(
    store: LocalFactorStore,
    factor_ids: list[str],
    ts_codes: list[str],
    trade_dates: list[str],
    method: str,
) -> torch.Tensor:
    if method not in COMPOSITE_METHODS:
        raise ValueError(f"unsupported composite method: {method}")
    if not factor_ids:
        return torch.zeros((len(ts_codes), len(trade_dates)), dtype=torch.float32)

    matrices = [
        store.load_factor_values_matrix(factor_id, ts_codes=ts_codes, trade_dates=trade_dates, device="cpu")
        for factor_id in factor_ids
    ]
    prepared = [_cs_rank(matrix) if method == "rank_average" else _cs_zscore(matrix) for matrix in matrices]
    stacked = torch.stack(prepared, dim=0)
    if method in {"equal_weight", "rank_average"}:
        return _finite(stacked.mean(dim=0))

    weights = torch.tensor(_score_weights(store, factor_ids), dtype=torch.float32).view(-1, 1, 1)
    return _finite((stacked * weights).sum(dim=0))


def register_composite_factor(
    store: LocalFactorStore,
    factor_ids: list[str],
    ts_codes: list[str],
    trade_dates: list[str],
    values: torch.Tensor,
    method: str,
    batch_id: str | None = None,
    created_at: str | None = None,
    metrics: dict[str, float] | None = None,
) -> dict[str, Any]:
    if method not in COMPOSITE_METHODS:
        raise ValueError(f"unsupported composite method: {method}")
    created_at = created_at or datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    formula_names = ["COMPOSITE", method, *factor_ids]
    formula_hash = stable_formula_hash(
        formula_tokens=[],
        formula_names=formula_names,
        feature_version=FEATURE_VERSION,
        operator_version=OPERATOR_VERSION,
    )
    factor_id = make_factor_id(formula_hash)
    existing = store.find_factor_by_hash(formula_hash)
    if existing is None:
        record = FactorRecord(
            factor_id=factor_id,
            formula=formula_names,
            formula_tokens=[],
            formula_hash=formula_hash,
            feature_version=FEATURE_VERSION,
            operator_version=OPERATOR_VERSION,
            lookback_days=1,
            created_at=created_at,
            status="approved",
            description=f"Composite factor built with {method}",
            metrics=metrics,
            metadata={
                "type": "composite",
                "component_factor_ids": factor_ids,
                "composite_method": method,
            },
            parent_factor_ids=factor_ids,
            factor_type="composite",
            batch_id=batch_id,
        )
        store.save_factor(record)
    else:
        factor_id = existing.factor_id

    value_result = store.save_factor_values(factor_id, ts_codes, trade_dates, values)
    return {
        "factor_id": factor_id,
        "factor_values_path": value_result.path,
        "component_factor_ids": factor_ids,
        "composite_method": method,
        "formula_hash": formula_hash,
        "skipped_existing": existing is not None,
    }


def _factor_score(record: FactorRecord) -> float:
    if isinstance(record.metrics, dict):
        value = record.metrics.get("score", 0.0)
        try:
            score = float(value)
            return score if math.isfinite(score) else 0.0
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _score_weights(store: LocalFactorStore, factor_ids: list[str]) -> list[float]:
    score_by_id = {record.factor_id: max(_factor_score(record), 0.0) for record in store.load_factors()}
    scores = [score_by_id.get(factor_id, 0.0) for factor_id in factor_ids]
    total = sum(scores)
    if total <= 1e-12:
        return [1.0 / len(factor_ids) for _ in factor_ids]
    return [score / total for score in scores]


def _records_to_union_matrix(records) -> torch.Tensor:
    ts_codes = sorted({record.ts_code for record in records})
    trade_dates = sorted({record.trade_date for record in records})
    matrix = torch.zeros((len(ts_codes), len(trade_dates)), dtype=torch.float32)
    stock_index = {ts_code: idx for idx, ts_code in enumerate(ts_codes)}
    date_index = {trade_date: idx for idx, trade_date in enumerate(trade_dates)}
    for record in records:
        if record.value is None:
            continue
        matrix[stock_index[record.ts_code], date_index[record.trade_date]] = float(record.value)
    return matrix


def _aligned_correlation(left_records, right_records) -> float:
    ts_codes = sorted({record.ts_code for record in left_records} | {record.ts_code for record in right_records})
    trade_dates = sorted({record.trade_date for record in left_records} | {record.trade_date for record in right_records})
    left = _records_to_matrix(left_records, ts_codes, trade_dates)
    right = _records_to_matrix(right_records, ts_codes, trade_dates)
    return factor_correlation(left, right)


def _records_to_matrix(records, ts_codes: list[str], trade_dates: list[str]) -> torch.Tensor:
    matrix = torch.zeros((len(ts_codes), len(trade_dates)), dtype=torch.float32)
    stock_index = {ts_code: idx for idx, ts_code in enumerate(ts_codes)}
    date_index = {trade_date: idx for idx, trade_date in enumerate(trade_dates)}
    for record in records:
        if record.value is None:
            continue
        matrix[stock_index[record.ts_code], date_index[record.trade_date]] = float(record.value)
    return matrix


def _cs_zscore(matrix: torch.Tensor) -> torch.Tensor:
    clean = _finite(matrix)
    mean = clean.mean(dim=0, keepdim=True)
    std = clean.std(dim=0, keepdim=True, unbiased=False)
    return _finite((clean - mean) / torch.clamp(std, min=1e-6))


def _cs_rank(matrix: torch.Tensor) -> torch.Tensor:
    clean = _finite(matrix)
    order = clean.argsort(dim=0)
    ranks = torch.zeros_like(clean)
    values = torch.arange(clean.shape[0], dtype=clean.dtype).unsqueeze(1)
    ranks.scatter_(0, order, values.expand_as(clean))
    return _finite(ranks / max(clean.shape[0] - 1, 1))


def _finite(matrix: torch.Tensor) -> torch.Tensor:
    return torch.nan_to_num(matrix.to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)
