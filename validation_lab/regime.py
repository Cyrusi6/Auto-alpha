"""Regime robustness diagnostics."""

from __future__ import annotations

import torch

from .metrics import _metrics_for_dates
from .models import RegimeValidationResult


def run_regime_validation(
    factors: torch.Tensor,
    target_ret: torch.Tensor,
    trade_dates: list[str],
    raw_data_cache: dict[str, torch.Tensor],
) -> tuple[list[RegimeValidationResult], dict]:
    if not trade_dates:
        return [], {"regime_count": 0, "regime_pass_ratio": 0.0}
    market_ret = target_ret.detach().float().cpu().mean(dim=0)
    volatility = target_ret.detach().float().cpu().std(dim=0)
    turnover = raw_data_cache.get("turnover_rate")
    turnover_vec = turnover.detach().float().cpu().mean(dim=0) if turnover is not None else torch.zeros_like(market_ret)
    limit_heavy = raw_data_cache.get("limit_up_flag")
    limit_vec = limit_heavy.detach().float().cpu().mean(dim=0) if limit_heavy is not None else torch.zeros_like(market_ret)
    buckets = {
        "market_return_up": [d for d, v in zip(trade_dates, market_ret.tolist()) if v > 0],
        "market_return_down": [d for d, v in zip(trade_dates, market_ret.tolist()) if v <= 0],
        "high_vol": _above_median(trade_dates, volatility),
        "low_vol": _below_or_equal_median(trade_dates, volatility),
        "high_turnover": _above_median(trade_dates, turnover_vec),
        "low_turnover": _below_or_equal_median(trade_dates, turnover_vec),
        "limit_heavy": _above_median(trade_dates, limit_vec),
        "non_limit_heavy": _below_or_equal_median(trade_dates, limit_vec),
    }
    date_index = {date: idx for idx, date in enumerate(trade_dates)}
    results = []
    for name, dates in buckets.items():
        metrics = _metrics_for_dates(factors, target_ret, dates, date_index)
        passed = bool(dates) and metrics.get("out_of_sample_score", 0.0) >= -1.0
        reason = "" if dates else "insufficient_data"
        results.append(RegimeValidationResult(name, dates, metrics, passed, reason))
    pass_ratio = sum(item.passed for item in results) / len(results) if results else 0.0
    return results, {"regime_count": len(results), "regime_pass_ratio": float(pass_ratio)}


def _above_median(dates: list[str], values: torch.Tensor) -> list[str]:
    med = float(values.median().item()) if values.numel() else 0.0
    return [date for date, value in zip(dates, values.tolist()) if value > med]


def _below_or_equal_median(dates: list[str], values: torch.Tensor) -> list[str]:
    med = float(values.median().item()) if values.numel() else 0.0
    return [date for date, value in zip(dates, values.tolist()) if value <= med]
