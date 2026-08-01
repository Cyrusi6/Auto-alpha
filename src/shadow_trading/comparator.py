"""Shadow comparison helpers."""

from __future__ import annotations


def summarize_shadow_vs_production(shadow_summary: dict, production_summary: dict | None = None) -> dict:
    production_summary = production_summary or {}
    return {
        "shadow_fill_rate": float(shadow_summary.get("shadow_fill_rate", 0.0) or 0.0),
        "production_fill_rate": float(production_summary.get("fill_rate", 0.0) or 0.0),
        "fill_rate_diff": float(shadow_summary.get("shadow_fill_rate", 0.0) or 0.0) - float(production_summary.get("fill_rate", 0.0) or 0.0),
        "shadow_order_count": int(shadow_summary.get("shadow_order_count", 0) or 0),
        "production_order_count": int(production_summary.get("n_orders", 0) or 0),
    }
