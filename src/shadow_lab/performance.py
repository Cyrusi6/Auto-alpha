"""Performance summaries for shadow lab."""

from __future__ import annotations

import math

from .models import ShadowDaySummary


def summarize_shadow_performance(days: list[ShadowDaySummary]) -> dict[str, float | int]:
    returns = [float(day.daily_return or 0.0) for day in days]
    cumulative = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for ret in returns:
        cumulative *= 1.0 + ret
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative / peak - 1.0 if peak else 0.0)
    mean = sum(returns) / len(returns) if returns else 0.0
    variance = sum((ret - mean) ** 2 for ret in returns) / len(returns) if returns else 0.0
    fill_rates = [float(day.shadow_fill_rate or 0.0) for day in days]
    rejected = sum(int(day.rejected_count or 0) for day in days)
    fills = sum(int(day.fill_count or 0) for day in days)
    return {
        "shadow_day_count": len(days),
        "shadow_cumulative_return": cumulative - 1.0,
        "shadow_max_drawdown": max_drawdown,
        "shadow_return_volatility": math.sqrt(max(variance, 0.0)),
        "shadow_average_fill_rate": sum(fill_rates) / len(fill_rates) if fill_rates else 0.0,
        "shadow_order_rejection_rate": rejected / fills if fills else 0.0,
    }
