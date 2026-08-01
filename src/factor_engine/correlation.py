"""Factor correlation helpers for local factor stores."""

from __future__ import annotations

from typing import Any

import torch

from factor_store import LocalFactorStore
from factor_store.models import FactorValueRecord


def factor_correlation(x: torch.Tensor, y: torch.Tensor) -> float:
    x_clean = _finite(x).reshape(-1)
    y_clean = _finite(y).reshape(-1)
    mask = torch.isfinite(x.reshape(-1)) & torch.isfinite(y.reshape(-1))
    if int(mask.sum().item()) < 2:
        return 0.0
    x_valid = x_clean[mask]
    y_valid = y_clean[mask]
    x_centered = x_valid - x_valid.mean()
    y_centered = y_valid - y_valid.mean()
    denom = x_centered.norm() * y_centered.norm()
    if float(denom.item()) <= 1e-12:
        return 0.0
    return float((x_centered * y_centered).sum().item() / denom.item())


def max_abs_correlation(candidate: torch.Tensor, existing_matrices: list[torch.Tensor]) -> float:
    if not existing_matrices:
        return 0.0
    return float(max(abs(factor_correlation(candidate, matrix)) for matrix in existing_matrices))


def factor_correlation_matrix(
    factor_matrices: dict[str, torch.Tensor] | list[torch.Tensor],
    factor_ids: list[str] | None = None,
) -> dict[str, dict[str, float]]:
    if isinstance(factor_matrices, dict):
        ids = list(factor_matrices.keys())
        matrices = [factor_matrices[factor_id] for factor_id in ids]
    else:
        matrices = list(factor_matrices)
        ids = factor_ids or [f"factor_{idx}" for idx in range(len(matrices))]
    result: dict[str, dict[str, float]] = {}
    for row_id, row_matrix in zip(ids, matrices):
        result[row_id] = {}
        for col_id, col_matrix in zip(ids, matrices):
            result[row_id][col_id] = float(factor_correlation(row_matrix, col_matrix))
    return result


def pairwise_correlation_table(
    factor_matrices: dict[str, torch.Tensor] | list[torch.Tensor],
    factor_ids: list[str] | None = None,
) -> list[dict[str, float | str]]:
    matrix = factor_correlation_matrix(factor_matrices, factor_ids)
    ids = list(matrix.keys())
    rows: list[dict[str, float | str]] = []
    for left_idx, left_id in enumerate(ids):
        for right_id in ids[left_idx + 1 :]:
            corr = float(matrix[left_id][right_id])
            rows.append(
                {
                    "factor_id_1": left_id,
                    "factor_id_2": right_id,
                    "correlation": corr,
                    "abs_correlation": abs(corr),
                }
            )
    rows.sort(key=lambda row: float(row["abs_correlation"]), reverse=True)
    return rows


def load_existing_factor_matrices(
    store: LocalFactorStore,
    factor_ids: list[str],
    ts_codes: list[str],
    trade_dates: list[str],
    device: torch.device | str | None = None,
) -> dict[str, torch.Tensor]:
    return {
        factor_id: factor_values_to_matrix(
            store.load_factor_values(factor_id),
            ts_codes=ts_codes,
            trade_dates=trade_dates,
            device=device,
        )
        for factor_id in factor_ids
    }


def find_similar_factors(
    candidate: torch.Tensor,
    store: LocalFactorStore,
    ts_codes: list[str],
    trade_dates: list[str],
    threshold: float = 0.8,
    device: torch.device | str | None = None,
) -> list[dict[str, Any]]:
    factors = store.load_factors()
    factor_ids = [record.factor_id for record in factors]
    matrices = load_existing_factor_matrices(store, factor_ids, ts_codes, trade_dates, device=device)
    similar: list[dict[str, Any]] = []
    for factor_id, matrix in matrices.items():
        corr = factor_correlation(candidate, matrix)
        if abs(corr) >= threshold:
            similar.append(
                {
                    "factor_id": factor_id,
                    "correlation": float(corr),
                    "abs_correlation": float(abs(corr)),
                }
            )
    similar.sort(key=lambda item: item["abs_correlation"], reverse=True)
    return similar


def factor_values_to_matrix(
    records: list[FactorValueRecord],
    ts_codes: list[str],
    trade_dates: list[str],
    device: torch.device | str | None = None,
) -> torch.Tensor:
    target_device = torch.device(device) if device is not None else None
    matrix = torch.zeros((len(ts_codes), len(trade_dates)), dtype=torch.float32, device=target_device)
    stock_index = {ts_code: idx for idx, ts_code in enumerate(ts_codes)}
    date_index = {trade_date: idx for idx, trade_date in enumerate(trade_dates)}
    for record in records:
        stock_idx = stock_index.get(record.ts_code)
        date_idx = date_index.get(record.trade_date)
        if stock_idx is None or date_idx is None or record.value is None:
            continue
        matrix[stock_idx, date_idx] = float(record.value)
    return matrix


def _finite(x: torch.Tensor) -> torch.Tensor:
    return torch.nan_to_num(x.to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)
