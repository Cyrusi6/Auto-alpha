"""Portfolio target construction."""

from __future__ import annotations

import math
from typing import Any

import torch

from .models import TargetPosition


def _to_tensor(values: Any) -> torch.Tensor:
    if hasattr(values, "detach"):
        return values.detach().cpu()
    return torch.tensor(values, dtype=torch.float32)


def build_long_only_targets(
    factors,
    ts_codes: list[str],
    trade_dates: list[str],
    top_n: int = 20,
    max_weight: float = 0.10,
) -> list[list[TargetPosition]]:
    matrix = _to_tensor(factors).to(dtype=torch.float32)
    targets_by_date: list[list[TargetPosition]] = []
    for date_idx, trade_date in enumerate(trade_dates):
        values = matrix[:, date_idx]
        valid_indices = [idx for idx, value in enumerate(values.tolist()) if math.isfinite(float(value))]
        valid_indices.sort(key=lambda idx: float(values[idx].item()), reverse=True)
        selected = valid_indices[: max(0, top_n)]
        if not selected:
            targets_by_date.append([])
            continue
        weight = min(max_weight, 1.0 / len(selected))
        targets_by_date.append(
            [
                TargetPosition(
                    trade_date=trade_date,
                    ts_code=ts_codes[idx],
                    target_weight=float(weight),
                    factor_value=float(values[idx].item()),
                )
                for idx in selected
            ]
        )
    return targets_by_date


def targets_to_weight_matrix(
    targets_by_date: list[list[TargetPosition]],
    ts_codes: list[str],
    trade_dates: list[str],
) -> torch.Tensor:
    code_index = {ts_code: idx for idx, ts_code in enumerate(ts_codes)}
    date_index = {trade_date: idx for idx, trade_date in enumerate(trade_dates)}
    weights = torch.zeros((len(ts_codes), len(trade_dates)), dtype=torch.float32)
    for targets in targets_by_date:
        for target in targets:
            if target.ts_code in code_index and target.trade_date in date_index:
                weights[code_index[target.ts_code], date_index[target.trade_date]] = float(target.target_weight)
    return weights
