"""Validity propagation rules for factor DSL operators."""

from __future__ import annotations

import torch


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
