"""Helpers to expose shadow lab summaries as readiness inputs."""

from __future__ import annotations

from typing import Any


def build_readiness_inputs(shadow_lab_report: dict[str, Any]) -> dict[str, Any]:
    performance = shadow_lab_report.get("performance_summary", {}) if isinstance(shadow_lab_report.get("performance_summary"), dict) else {}
    drift = shadow_lab_report.get("drift_summary", {}) if isinstance(shadow_lab_report.get("drift_summary"), dict) else {}
    return {
        "shadow_day_count": int(performance.get("shadow_day_count", 0) or 0),
        "shadow_average_fill_rate": float(performance.get("shadow_average_fill_rate", 0.0) or 0.0),
        "shadow_order_rejection_rate": float(performance.get("shadow_order_rejection_rate", 0.0) or 0.0),
        "shadow_target_weight_drift": float(drift.get("shadow_target_weight_drift", 0.0) or 0.0),
        "shadow_position_weight_drift": float(drift.get("shadow_position_weight_drift", 0.0) or 0.0),
    }
