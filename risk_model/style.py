"""Barra-like style factor exposures for A-share data."""

from __future__ import annotations

import torch


STYLE_FACTOR_NAMES = ("size", "value", "momentum", "volatility", "liquidity", "quality", "growth")


def build_style_exposures(loader) -> dict[str, torch.Tensor]:
    raw = loader.raw_data_cache
    close = _field(raw, "adjusted_close", "close")
    ret_5d = _rolling_return(close, 5)
    returns = torch.nan_to_num(loader.target_ret.detach().cpu().to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)
    exposures = {
        "size": _cs_process(raw.get("log_mkt_cap", torch.log1p(torch.clamp(raw["total_mv"], min=0.0)))),
        "value": _cs_process(-0.5 * raw.get("pb", torch.zeros_like(close)) - 0.5 * raw.get("pe_ttm", torch.zeros_like(close))),
        "momentum": _cs_process(ret_5d),
        "volatility": _cs_process(_rolling_std(returns, 5)),
        "liquidity": _cs_process(torch.log1p(torch.clamp(raw.get("amount", torch.zeros_like(close)), min=0.0)) + raw.get("turnover_rate", torch.zeros_like(close))),
        "quality": _cs_process(raw.get("roe", torch.zeros_like(close))),
        "growth": _cs_process(raw.get("revenue_yoy", torch.zeros_like(close))),
    }
    return {name: torch.nan_to_num(value.to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0) for name, value in exposures.items()}


def _field(raw: dict[str, torch.Tensor], preferred: str, fallback: str) -> torch.Tensor:
    return raw.get(preferred, raw[fallback]).detach().cpu().to(dtype=torch.float32)


def _cs_process(x: torch.Tensor) -> torch.Tensor:
    clean = torch.nan_to_num(x.detach().cpu().to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)
    median = clean.median(dim=0, keepdim=True).values
    centered = clean - median
    mad = centered.abs().median(dim=0, keepdim=True).values
    scale = torch.where(mad < 1e-6, torch.ones_like(mad), mad)
    winsorized = torch.clamp(centered / scale, -5.0, 5.0)
    mean = winsorized.mean(dim=0, keepdim=True)
    std = winsorized.std(dim=0, keepdim=True, unbiased=False)
    return torch.nan_to_num((winsorized - mean) / torch.clamp(std, min=1e-6), nan=0.0, posinf=0.0, neginf=0.0)


def _rolling_return(close: torch.Tensor, window: int) -> torch.Tensor:
    clean = torch.clamp(close.detach().cpu().to(dtype=torch.float32), min=1e-6)
    result = torch.zeros_like(clean)
    if clean.shape[1] > window:
        result[:, window:] = torch.log(clean[:, window:] / clean[:, :-window])
    return torch.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)


def _rolling_std(x: torch.Tensor, window: int) -> torch.Tensor:
    if x.shape[1] <= 1:
        return torch.zeros_like(x)
    pad = torch.zeros((x.shape[0], max(window - 1, 0)), dtype=x.dtype)
    windows = torch.cat([pad, x], dim=1).unfold(1, window, 1)
    return torch.nan_to_num(windows.std(dim=-1, unbiased=False), nan=0.0, posinf=0.0, neginf=0.0)
