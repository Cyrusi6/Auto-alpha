"""Load multi-day shadow artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ShadowDaySummary


def load_shadow_inputs(
    replay_report_path: str | Path | None = None,
    replay_dir: str | Path | None = None,
    shadow_root_dir: str | Path | None = None,
) -> list[ShadowDaySummary]:
    day_rows: list[dict[str, Any]] = []
    if replay_report_path:
        payload = _read_json(Path(replay_report_path))
        day_rows.extend(payload.get("day_results", []) if isinstance(payload.get("day_results"), list) else [])
    if replay_dir and not day_rows:
        payload = _read_json(Path(replay_dir) / "production_replay_report.json")
        day_rows.extend(payload.get("day_results", []) if isinstance(payload.get("day_results"), list) else [])
    summaries: list[ShadowDaySummary] = []
    for row in day_rows:
        trade_date = str(row.get("trade_date") or "")
        paths = row.get("paths") if isinstance(row.get("paths"), dict) else {}
        shadow_report = _read_json(Path(paths.get("shadow_run_report_path", ""))) if paths.get("shadow_run_report_path") else {}
        shadow_drift = _read_json(Path(paths.get("shadow_drift_report_path", ""))) if paths.get("shadow_drift_report_path") else {}
        if not shadow_report and shadow_root_dir:
            shadow_report = _read_json(Path(shadow_root_dir) / trade_date / "shadow_run_report.json")
            shadow_drift = _read_json(Path(shadow_root_dir) / trade_date / "shadow_drift_report.json")
        summaries.append(_summary_from_payloads(row, shadow_report, shadow_drift))
    if not summaries and shadow_root_dir:
        root = Path(shadow_root_dir)
        for report_path in sorted(root.glob("*/shadow_run_report.json")):
            trade_date = report_path.parent.name
            summaries.append(_summary_from_payloads({"trade_date": trade_date, "paths": {"shadow_run_report_path": str(report_path)}}, _read_json(report_path), _read_json(report_path.parent / "shadow_drift_report.json")))
    return summaries


def _summary_from_payloads(day_row: dict[str, Any], shadow_report: dict[str, Any], shadow_drift: dict[str, Any]) -> ShadowDaySummary:
    summary = shadow_report.get("summary") if isinstance(shadow_report.get("summary"), dict) else {}
    drift_summary = shadow_drift.get("summary") if isinstance(shadow_drift.get("summary"), dict) else {}
    fills = shadow_report.get("fills") if isinstance(shadow_report.get("fills"), list) else []
    orders = shadow_report.get("orders") if isinstance(shadow_report.get("orders"), list) else []
    snapshots = shadow_report.get("snapshots") if isinstance(shadow_report.get("snapshots"), list) else []
    last_snapshot = snapshots[-1] if snapshots else {}
    rejected = sum(1 for fill in fills if str(fill.get("status", "")).upper() == "REJECTED")
    return ShadowDaySummary(
        trade_date=str(day_row.get("trade_date") or shadow_report.get("trade_date") or ""),
        production_run_id=day_row.get("production_run_id") or shadow_report.get("production_run_id"),
        status=str(shadow_report.get("status") or day_row.get("status") or ""),
        shadow_fill_rate=float(summary.get("fill_rate", day_row.get("shadow_fill_rate", 0.0)) or 0.0),
        order_count=int(summary.get("order_count", len(orders)) or 0),
        fill_count=int(summary.get("fill_count", len(fills)) or 0),
        rejected_count=int(summary.get("rejected_count", rejected) or 0),
        target_weight_drift=float(drift_summary.get("target_weight_drift", day_row.get("summary", {}).get("target_weight_drift", 0.0) if isinstance(day_row.get("summary"), dict) else 0.0) or 0.0),
        position_weight_drift=float(drift_summary.get("position_weight_drift", day_row.get("summary", {}).get("position_weight_drift", 0.0) if isinstance(day_row.get("summary"), dict) else 0.0) or 0.0),
        equity=float(last_snapshot.get("equity", summary.get("equity", 0.0)) or 0.0),
        daily_return=float(summary.get("daily_return", 0.0) or 0.0),
        paths=dict(day_row.get("paths") or {}),
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not str(path) or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
