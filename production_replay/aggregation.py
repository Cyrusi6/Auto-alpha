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
    go_live_statuses = [str(row.get("go_live_status") or "") for row in rows if row.get("go_live_status")]
    broker_uat_statuses = [str(row.get("broker_uat_status") or "") for row in rows if row.get("broker_uat_status")]
    compliance_gap_count = sum(int(row.get("compliance_gap_count") or 0) for row in rows)
    file_outbox_rows = [row for row in rows if row.get("run_mode") == "file_outbox_dry_run"]
    file_roundtrip_errors = 0
    real_submit_detected = False
    for row in file_outbox_rows:
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        broker_summary = summary.get("broker_summary") if isinstance(summary.get("broker_summary"), dict) else {}
        file_roundtrip_errors += int(broker_summary.get("roundtrip_error_count", 0) or 0)
        real_submit_detected = real_submit_detected or bool(broker_summary.get("file_outbox_real_submit_detected"))
    return {
        "replay_day_count": total,
        "file_outbox_day_count": len(file_outbox_rows),
        "file_outbox_roundtrip_error_count": file_roundtrip_errors,
        "file_outbox_real_submit_detected": real_submit_detected,
        "replay_success_day_count": success,
        "replay_warning_day_count": warning,
        "replay_failed_day_count": failed,
        "replay_blocked_day_count": blocked,
        "replay_skipped_day_count": skipped,
        "close_day_success_count": sum(1 for row in rows if row.get("close_status") == "closed"),
        "average_fill_rate": avg_fill_rate,
        "broker_unfilled_value": broker_unfilled,
        "go_live_status": go_live_statuses[-1] if go_live_statuses else "",
        "broker_uat_status": broker_uat_statuses[-1] if broker_uat_statuses else "",
        "compliance_gap_count": compliance_gap_count,
        "status": "failed" if failed else "blocked" if blocked else "warning" if warning else "success",
    }
