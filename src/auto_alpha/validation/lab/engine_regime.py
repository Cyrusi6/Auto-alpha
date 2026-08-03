"""Regime robustness diagnostics."""

from __future__ import annotations

import math
import torch

from auto_alpha.validation.lab.engine_metrics import _metrics_for_dates
from auto_alpha.validation.lab.engine_models import RegimeValidationResult


def run_regime_validation(
    factors: torch.Tensor,
    target_ret: torch.Tensor,
    trade_dates: list[str],
    raw_data_cache: dict[str, torch.Tensor],
) -> tuple[list[RegimeValidationResult], dict]:
    if not trade_dates:
        return [], {"regime_count": 0, "regime_pass_ratio": 0.0}
    governed_target = target_ret.detach().float().cpu()
    market_ret = _nanmean(governed_target, dim=0)
    volatility = _nanstd(governed_target, dim=0)
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
        score = metrics.get("out_of_sample_score")
        passed = bool(dates) and bool(metrics.get("evaluable")) and score is not None and math.isfinite(float(score)) and float(score) >= -1.0
        reason = "" if passed else ("insufficient_data" if not dates else "no_evaluable_target_samples")
        results.append(RegimeValidationResult(name, dates, metrics, passed, reason))
    pass_ratio = sum(item.passed for item in results) / len(results) if results else 0.0
    return results, {"regime_count": len(results), "regime_pass_ratio": float(pass_ratio)}


def _above_median(dates: list[str], values: torch.Tensor) -> list[str]:
    finite = values[torch.isfinite(values)]
    med = float(finite.median().item()) if finite.numel() else 0.0
    return [date for date, value in zip(dates, values.tolist()) if math.isfinite(float(value)) and value > med]


def _below_or_equal_median(dates: list[str], values: torch.Tensor) -> list[str]:
    finite = values[torch.isfinite(values)]
    med = float(finite.median().item()) if finite.numel() else 0.0
    return [date for date, value in zip(dates, values.tolist()) if math.isfinite(float(value)) and value <= med]


def _nanmean(values: torch.Tensor, dim: int) -> torch.Tensor:
    valid = torch.isfinite(values)
    numerator = torch.where(valid, values, torch.zeros_like(values)).sum(dim=dim)
    denominator = valid.sum(dim=dim)
    return torch.where(denominator > 0, numerator / denominator.clamp(min=1), torch.full_like(numerator, float("nan")))


def _nanstd(values: torch.Tensor, dim: int) -> torch.Tensor:
    center = _nanmean(values, dim=dim)
    valid = torch.isfinite(values)
    expanded = center.unsqueeze(dim)
    squared = torch.where(valid, (values - expanded) ** 2, torch.zeros_like(values))
    denominator = valid.sum(dim=dim)
    return torch.where(denominator > 0, torch.sqrt(squared.sum(dim=dim) / denominator.clamp(min=1)), torch.full_like(center, float("nan")))
