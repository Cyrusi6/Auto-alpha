"""A-share factor DSL operators."""

from __future__ import annotations

import torch


def _finite(x: torch.Tensor) -> torch.Tensor:
    return torch.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)


def safe_div(x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
    denom = torch.where(torch.abs(y) < 1e-6, torch.full_like(y, 1e-6), y)
    return _finite(x / denom)


def ts_delay(x: torch.Tensor, periods: int) -> torch.Tensor:
    if periods <= 0:
        return _finite(x)
    pad = torch.zeros((x.shape[0], periods), dtype=x.dtype, device=x.device)
    return _finite(torch.cat([pad, x[:, :-periods]], dim=1))


def ts_delta(x: torch.Tensor, periods: int) -> torch.Tensor:
    return _finite(x - ts_delay(x, periods))


def ts_mean(x: torch.Tensor, window: int) -> torch.Tensor:
    if window <= 1:
        return _finite(x)
    clean = _finite(x)
    pad = torch.zeros((clean.shape[0], window - 1), dtype=clean.dtype, device=clean.device)
    windows = torch.cat([pad, clean], dim=1).unfold(1, window, 1)
    return _finite(windows.mean(dim=-1))


def ts_std(x: torch.Tensor, window: int) -> torch.Tensor:
    if window <= 1:
        return torch.zeros_like(x)
    clean = _finite(x)
    pad = torch.zeros((clean.shape[0], window - 1), dtype=clean.dtype, device=clean.device)
    windows = torch.cat([pad, clean], dim=1).unfold(1, window, 1)
    return _finite(windows.std(dim=-1, unbiased=False))


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


OPS_CONFIG = [
    ("ADD", lambda x, y: _finite(x + y), 2),
    ("SUB", lambda x, y: _finite(x - y), 2),
    ("MUL", lambda x, y: _finite(x * y), 2),
    ("DIV", safe_div, 2),
    ("NEG", lambda x: _finite(-x), 1),
    ("ABS", lambda x: _finite(torch.abs(x)), 1),
    ("SIGN", lambda x: _finite(torch.sign(x)), 1),
    ("DELAY1", lambda x: ts_delay(x, 1), 1),
    ("DELTA1", lambda x: ts_delta(x, 1), 1),
    ("TS_MEAN3", lambda x: ts_mean(x, 3), 1),
    ("TS_STD3", lambda x: ts_std(x, 3), 1),
    ("TS_ZSCORE3", lambda x: ts_zscore(x, 3), 1),
    ("CS_RANK", cs_rank, 1),
    ("CS_ZSCORE", cs_zscore, 1),
    ("WINSORIZE", winsorize, 1),
]
