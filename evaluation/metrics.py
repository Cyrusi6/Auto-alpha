"""Split-aware factor metrics."""

from __future__ import annotations

import torch

from .split import TimeSeriesSplitResult


def evaluate_by_date_mask(
    evaluator,
    factors: torch.Tensor,
    raw_data: dict[str, torch.Tensor],
    target_ret: torch.Tensor,
    trade_dates: list[str],
    selected_dates: list[str],
) -> dict[str, float]:
    selected = set(selected_dates)
    indices = [idx for idx, trade_date in enumerate(trade_dates) if trade_date in selected]
    if indices:
        index_tensor = torch.tensor(indices, dtype=torch.long, device=factors.device)
        split_factors = factors.index_select(1, index_tensor)
        split_target = target_ret.index_select(1, index_tensor)
        split_raw = {
            key: _select_dates(value, index_tensor)
            for key, value in raw_data.items()
        }
    else:
        split_factors = factors[:, :0]
        split_target = target_ret[:, :0]
        split_raw = {key: _empty_dates(value) for key, value in raw_data.items()}

    return {key: float(value) for key, value in evaluator.evaluate(split_factors, split_raw, split_target).to_dict().items()}


def _select_dates(value, index_tensor: torch.Tensor):
    if isinstance(value, torch.Tensor) and value.ndim >= 2 and value.shape[1] >= int(index_tensor.max().item()) + 1:
        return value.index_select(1, index_tensor.to(device=value.device))
    return value


def _empty_dates(value):
    if isinstance(value, torch.Tensor) and value.ndim >= 2:
        return value[:, :0]
    return value


def evaluate_by_splits(
    evaluator,
    factors: torch.Tensor,
    raw_data: dict[str, torch.Tensor],
    target_ret: torch.Tensor,
    trade_dates: list[str],
    split_result: TimeSeriesSplitResult,
) -> dict[str, dict[str, float]]:
    return {
        "train": evaluate_by_date_mask(
            evaluator, factors, raw_data, target_ret, trade_dates, split_result.train_dates
        ),
        "valid": evaluate_by_date_mask(
            evaluator, factors, raw_data, target_ret, trade_dates, split_result.valid_dates
        ),
        "test": evaluate_by_date_mask(
            evaluator, factors, raw_data, target_ret, trade_dates, split_result.test_dates
        ),
        "all": evaluate_by_date_mask(evaluator, factors, raw_data, target_ret, trade_dates, trade_dates),
    }
