"""Replay aggregation utilities."""

from __future__ import annotations

from typing import Any

from .models import ProductionReplayDayResult


def aggregate_replay_days(day_results: list[ProductionReplayDayResult | dict[str, Any]]) -> dict[str, Any]:
    rows = [item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in day_results]
    total = len(rows)
    success = sum(1 for row in rows if row.get("status") == "success")
    warning = sum(1 for row in rows if row.get("status") == "warning")
    failed = sum(1 for row in rows if row.get("status") == "failed")
    blocked = sum(1 for row in rows if row.get("status") == "blocked")
    skipped = sum(1 for row in rows if row.get("status") == "skipped")
    fill_rates = [float(row.get("paper_fill_rate") or row.get("shadow_fill_rate") or 0.0) for row in rows]
    avg_fill_rate = sum(fill_rates) / len(fill_rates) if fill_rates else 0.0
    broker_unfilled = sum(float(row.get("broker_unfilled_value") or 0.0) for row in rows)
    return {
        "replay_day_count": total,
        "replay_success_day_count": success,
        "replay_warning_day_count": warning,
        "replay_failed_day_count": failed,
        "replay_blocked_day_count": blocked,
        "replay_skipped_day_count": skipped,
        "close_day_success_count": sum(1 for row in rows if row.get("close_status") == "closed"),
        "average_fill_rate": avg_fill_rate,
        "broker_unfilled_value": broker_unfilled,
        "status": "failed" if failed else "blocked" if blocked else "warning" if warning else "success",
    }
