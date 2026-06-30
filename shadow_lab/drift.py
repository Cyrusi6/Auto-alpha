"""Drift aggregation for shadow lab."""

from __future__ import annotations

from .models import ShadowDaySummary


def summarize_shadow_drift(days: list[ShadowDaySummary], threshold: float = 0.05) -> dict[str, float | int | bool]:
    target = [abs(float(day.target_weight_drift or 0.0)) for day in days]
    position = [abs(float(day.position_weight_drift or 0.0)) for day in days]
    max_target = max(target, default=0.0)
    max_position = max(position, default=0.0)
    return {
        "shadow_target_weight_drift": max_target,
        "shadow_position_weight_drift": max_position,
        "shadow_average_target_weight_drift": sum(target) / len(target) if target else 0.0,
        "shadow_average_position_weight_drift": sum(position) / len(position) if position else 0.0,
        "shadow_drift_breach_count": sum(1 for value in [*target, *position] if value > threshold),
        "drift_threshold": threshold,
        "passed": max(max_target, max_position) <= threshold,
    }
