"""Validity propagation rules for factor DSL operators."""

from __future__ import annotations

import torch

from .ops import get_operator_spec


def propagate_operator_validity(name: str, args: list[torch.Tensor], values: list[torch.Tensor]) -> torch.Tensor:
    if not args:
        raise ValueError("validity propagation requires inputs")
    name = str(name).upper()
    if name in {"ADD", "SUB", "MUL"}:
        return args[0] & args[1]
    if name == "DIV":
        return args[0] & args[1] & torch.isfinite(values[1]) & (torch.abs(values[1]) >= 1e-6)
    if name in {"NEG", "ABS", "SIGN", "WINSORIZE"}:
        return args[0]
    if name.startswith("DELAY"):
        return _delay(args[0], int(name.removeprefix("DELAY")))
    if name.startswith("DELTA"):
        periods = int(name.removeprefix("DELTA"))
        return args[0] & _delay(args[0], periods)
    if name.startswith("TS_CORR"):
        window = int(name.removeprefix("TS_CORR"))
        return _rolling_all(args[0] & args[1], window)
    if name.startswith("TS_"):
        window = int(name.rsplit("_", 1)[-1].removeprefix("ZSCORE").removeprefix("RANK").removeprefix("MEAN").removeprefix("STD").removeprefix("MIN").removeprefix("MAX"))
        return _rolling_all(args[0], window)
    if name in {"CS_RANK", "CS_ZSCORE"}:
        breadth_ok = args[0].sum(dim=0, keepdim=True) >= 2
        return args[0] & breadth_ok
    raise KeyError(f"missing validity rule for operator: {name}")


def execute_operator_with_validity(
    token: int,
    operator_offset: int,
    values: list[torch.Tensor],
    masks: list[torch.Tensor],
) -> tuple[torch.Tensor, torch.Tensor]:
    """Execute an operator without allowing invalid inputs into its statistics."""
    spec = get_operator_spec(token, operator_offset)
    name = spec.name.upper()
    valid = propagate_operator_validity(name, masks, values)
    if name == "CS_RANK":
        result = _masked_cs_rank(values[0], valid)
    elif name == "CS_ZSCORE":
        result = _masked_cs_zscore(values[0], valid)
    else:
        clean_values = [torch.where(mask, value, torch.zeros_like(value)) for value, mask in zip(values, masks, strict=True)]
        result = spec.func(*clean_values)
    valid = valid & torch.isfinite(result)
    return torch.where(valid, result, torch.zeros_like(result)), valid


def _masked_cs_rank(values: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    result = torch.zeros_like(values)
    for date_index in range(values.shape[1]):
        date_mask = mask[:, date_index]
        count = int(date_mask.sum().item())
        if count < 2:
            continue
        eligible = values[date_mask, date_index]
        order = torch.argsort(eligible, stable=True)
        sorted_values = eligible[order]
        sorted_ranks = torch.empty_like(sorted_values)
        start = 0
        while start < count:
            end = start + 1
            while end < count and bool(sorted_values[end] == sorted_values[start]):
                end += 1
            sorted_ranks[start:end] = (start + end - 1) / 2.0
            start = end
        ranks = torch.empty_like(sorted_ranks)
        ranks[order] = sorted_ranks / max(count - 1, 1)
        result[date_mask, date_index] = ranks
    return result


def _masked_cs_zscore(values: torch.Tensor, mask: torch.Tensor, limit: float = 5.0) -> torch.Tensor:
    masked = torch.where(mask, values, torch.zeros_like(values))
    count = mask.sum(dim=0, keepdim=True).clamp_min(1).to(values.dtype)
    mean = masked.sum(dim=0, keepdim=True) / count
    centered = torch.where(mask, values - mean, torch.zeros_like(values))
    variance = centered.square().sum(dim=0, keepdim=True) / count
    scale = torch.sqrt(variance).clamp_min(1e-6)
    return torch.clamp(centered / scale, -limit, limit)


def _delay(mask: torch.Tensor, periods: int) -> torch.Tensor:
    result = torch.zeros_like(mask, dtype=torch.bool)
    if periods <= 0:
        return mask.bool()
    if mask.shape[1] > periods:
        result[:, periods:] = mask[:, :-periods]
    return result


def _rolling_all(mask: torch.Tensor, window: int) -> torch.Tensor:
    result = torch.zeros_like(mask, dtype=torch.bool)
    if window <= 1:
        return mask.bool()
    if mask.shape[1] >= window:
        windows = mask.to(torch.int16).unfold(1, window, 1)
        result[:, window - 1 :] = windows.sum(dim=-1) == window
    return result
