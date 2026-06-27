"""A-share factor DSL operators and metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import torch


DEFAULT_OPERATOR_OFFSET = 11


@dataclass(frozen=True)
class OperatorSpec:
    name: str
    func: Callable
    arity: int
    lookback: int = 1
    complexity: int = 1


def _finite(x: torch.Tensor) -> torch.Tensor:
    return torch.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)


def safe_div(x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
    denom = torch.where(torch.abs(y) < 1e-6, torch.full_like(y, 1e-6), y)
    return _finite(x / denom)


def ts_delay(x: torch.Tensor, periods: int) -> torch.Tensor:
    if periods <= 0:
        return _finite(x)
    if x.shape[1] <= periods:
        return torch.zeros_like(x)
    pad = torch.zeros((x.shape[0], periods), dtype=x.dtype, device=x.device)
    return _finite(torch.cat([pad, x[:, :-periods]], dim=1))


def ts_delta(x: torch.Tensor, periods: int) -> torch.Tensor:
    return _finite(x - ts_delay(x, periods))


def _rolling_windows(x: torch.Tensor, window: int) -> torch.Tensor:
    clean = _finite(x)
    if window <= 1:
        return clean.unsqueeze(-1)
    pad = torch.zeros((clean.shape[0], window - 1), dtype=clean.dtype, device=clean.device)
    return torch.cat([pad, clean], dim=1).unfold(1, window, 1)


def ts_mean(x: torch.Tensor, window: int) -> torch.Tensor:
    if window <= 1:
        return _finite(x)
    return _finite(_rolling_windows(x, window).mean(dim=-1))


def ts_std(x: torch.Tensor, window: int) -> torch.Tensor:
    if window <= 1:
        return torch.zeros_like(x)
    return _finite(_rolling_windows(x, window).std(dim=-1, unbiased=False))


def ts_rank(x: torch.Tensor, window: int) -> torch.Tensor:
    windows = _rolling_windows(x, window)
    current = windows[:, :, -1:]
    ranks = (windows <= current).to(dtype=x.dtype).sum(dim=-1) - 1.0
    return _finite(ranks / max(window - 1, 1))


def ts_min(x: torch.Tensor, window: int) -> torch.Tensor:
    return _finite(_rolling_windows(x, window).min(dim=-1).values)


def ts_max(x: torch.Tensor, window: int) -> torch.Tensor:
    return _finite(_rolling_windows(x, window).max(dim=-1).values)


def ts_corr(x: torch.Tensor, y: torch.Tensor, window: int) -> torch.Tensor:
    x_windows = _rolling_windows(x, window)
    y_windows = _rolling_windows(y, window)
    x_centered = x_windows - x_windows.mean(dim=-1, keepdim=True)
    y_centered = y_windows - y_windows.mean(dim=-1, keepdim=True)
    numerator = (x_centered * y_centered).sum(dim=-1)
    denominator = torch.sqrt((x_centered.square().sum(dim=-1) * y_centered.square().sum(dim=-1)).clamp_min(1e-12))
    return winsorize(safe_div(numerator, denominator))


def ts_zscore(x: torch.Tensor, window: int) -> torch.Tensor:
    mean = ts_mean(x, window)
    std = ts_std(x, window)
    return winsorize(safe_div(x - mean, std + 1e-6))


def cs_rank(x: torch.Tensor) -> torch.Tensor:
    clean = _finite(x)
    order = clean.argsort(dim=0)
    ranks = torch.zeros_like(clean)
    values = torch.arange(clean.shape[0], dtype=clean.dtype, device=clean.device).unsqueeze(1)
    ranks.scatter_(0, order, values.expand_as(clean))
    denom = max(clean.shape[0] - 1, 1)
    return _finite(ranks / denom)


def cs_zscore(x: torch.Tensor) -> torch.Tensor:
    clean = _finite(x)
    mean = clean.mean(dim=0, keepdim=True)
    std = clean.std(dim=0, keepdim=True, unbiased=False)
    return winsorize(safe_div(clean - mean, std + 1e-6))


def winsorize(x: torch.Tensor, limit: float = 5.0) -> torch.Tensor:
    return torch.clamp(_finite(x), -limit, limit)


OPERATOR_SPECS: tuple[OperatorSpec, ...] = (
    OperatorSpec("ADD", lambda x, y: _finite(x + y), 2, lookback=1, complexity=1),
    OperatorSpec("SUB", lambda x, y: _finite(x - y), 2, lookback=1, complexity=1),
    OperatorSpec("MUL", lambda x, y: _finite(x * y), 2, lookback=1, complexity=2),
    OperatorSpec("DIV", safe_div, 2, lookback=1, complexity=2),
    OperatorSpec("NEG", lambda x: _finite(-x), 1, lookback=1, complexity=1),
    OperatorSpec("ABS", lambda x: _finite(torch.abs(x)), 1, lookback=1, complexity=1),
    OperatorSpec("SIGN", lambda x: _finite(torch.sign(x)), 1, lookback=1, complexity=1),
    OperatorSpec("DELAY1", lambda x: ts_delay(x, 1), 1, lookback=1, complexity=1),
    OperatorSpec("DELAY5", lambda x: ts_delay(x, 5), 1, lookback=5, complexity=2),
    OperatorSpec("DELTA1", lambda x: ts_delta(x, 1), 1, lookback=1, complexity=1),
    OperatorSpec("DELTA5", lambda x: ts_delta(x, 5), 1, lookback=5, complexity=2),
    OperatorSpec("TS_MEAN3", lambda x: ts_mean(x, 3), 1, lookback=3, complexity=2),
    OperatorSpec("TS_MEAN5", lambda x: ts_mean(x, 5), 1, lookback=5, complexity=2),
    OperatorSpec("TS_MEAN10", lambda x: ts_mean(x, 10), 1, lookback=10, complexity=3),
    OperatorSpec("TS_STD3", lambda x: ts_std(x, 3), 1, lookback=3, complexity=2),
    OperatorSpec("TS_STD5", lambda x: ts_std(x, 5), 1, lookback=5, complexity=2),
    OperatorSpec("TS_STD10", lambda x: ts_std(x, 10), 1, lookback=10, complexity=3),
    OperatorSpec("TS_ZSCORE3", lambda x: ts_zscore(x, 3), 1, lookback=3, complexity=3),
    OperatorSpec("TS_RANK5", lambda x: ts_rank(x, 5), 1, lookback=5, complexity=2),
    OperatorSpec("TS_RANK10", lambda x: ts_rank(x, 10), 1, lookback=10, complexity=3),
    OperatorSpec("TS_MIN5", lambda x: ts_min(x, 5), 1, lookback=5, complexity=2),
    OperatorSpec("TS_MAX5", lambda x: ts_max(x, 5), 1, lookback=5, complexity=2),
    OperatorSpec("TS_CORR5", lambda x, y: ts_corr(x, y, 5), 2, lookback=5, complexity=4),
    OperatorSpec("TS_CORR10", lambda x, y: ts_corr(x, y, 10), 2, lookback=10, complexity=5),
    OperatorSpec("CS_RANK", cs_rank, 1, lookback=1, complexity=1),
    OperatorSpec("CS_ZSCORE", cs_zscore, 1, lookback=1, complexity=2),
    OperatorSpec("WINSORIZE", winsorize, 1, lookback=1, complexity=1),
)

OPS_CONFIG = [(spec.name, spec.func, spec.arity) for spec in OPERATOR_SPECS]


def get_operator_spec(name_or_token: str | int, operator_offset: int = DEFAULT_OPERATOR_OFFSET) -> OperatorSpec:
    if isinstance(name_or_token, str):
        for spec in OPERATOR_SPECS:
            if spec.name == name_or_token:
                return spec
        raise KeyError(f"unknown operator: {name_or_token}")
    index = int(name_or_token)
    if index >= operator_offset:
        index -= operator_offset
    if index < 0 or index >= len(OPERATOR_SPECS):
        raise KeyError(f"unknown operator token: {name_or_token}")
    return OPERATOR_SPECS[index]


def operator_arity(token: int, operator_offset: int = DEFAULT_OPERATOR_OFFSET) -> int:
    return get_operator_spec(token, operator_offset).arity


def operator_lookback(token: int, operator_offset: int = DEFAULT_OPERATOR_OFFSET) -> int:
    return get_operator_spec(token, operator_offset).lookback


def operator_complexity(token: int, operator_offset: int = DEFAULT_OPERATOR_OFFSET) -> int:
    return get_operator_spec(token, operator_offset).complexity
