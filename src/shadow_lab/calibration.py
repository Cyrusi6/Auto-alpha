"""Calibration suggestions for shadow lab."""

from __future__ import annotations

from typing import Any


def build_calibration_suggestions(performance: dict[str, Any], drift: dict[str, Any]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    if float(performance.get("shadow_average_fill_rate", 1.0) or 0.0) < 0.8:
        suggestions.append({"code": "low_fill_rate", "severity": "warning", "message": "review participation limits or order slicing assumptions"})
    if float(performance.get("shadow_order_rejection_rate", 0.0) or 0.0) > 0.2:
        suggestions.append({"code": "high_rejection_rate", "severity": "warning", "message": "inspect limit and suspension constraints before live gate"})
    if not bool(drift.get("passed", True)):
        suggestions.append({"code": "weight_drift", "severity": "warning", "message": "tighten target/position reconciliation and rebalance rounding policy"})
    return suggestions
