"""Shadow trading report writers."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import ShadowPerformanceReport, ShadowRunReport


def write_shadow_report(report: ShadowRunReport, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    run_path = write_json_artifact(root / "shadow_run_report.json", payload, "shadow_run_report", "shadow_trading")
    md_path = root / "shadow_run_report.md"
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    orders_path = write_jsonl_artifact(root / "shadow_orders.jsonl", [item.to_dict() for item in report.orders], "shadow_orders", "shadow_trading")
    fills_path = write_jsonl_artifact(root / "shadow_fills.jsonl", [item.to_dict() for item in report.fills], "shadow_fills", "shadow_trading")
    positions_path = write_jsonl_artifact(root / "shadow_positions.jsonl", [item.to_dict() for item in report.positions], "shadow_positions", "shadow_trading")
    snapshots_path = write_jsonl_artifact(root / "shadow_account_snapshots.jsonl", [item.to_dict() for item in report.snapshots], "shadow_account_snapshots", "shadow_trading")
    drift_path = write_json_artifact(root / "shadow_drift_report.json", {"production_run_id": report.production_run_id, "trade_date": report.trade_date, "drift": [item.to_dict() for item in report.drift], "summary": report.summary}, "shadow_drift_report", "shadow_trading")
    performance = ShadowPerformanceReport(report.production_run_id, report.trade_date, {key: float(value) for key, value in report.summary.items() if isinstance(value, (int, float))})
    performance_path = write_json_artifact(root / "shadow_performance_report.json", performance.to_dict(), "shadow_performance_report", "shadow_trading")
    comparison_path = write_json_artifact(root / "shadow_vs_production_comparison.json", {"production_run_id": report.production_run_id, "summary": report.summary}, "shadow_vs_production_comparison", "shadow_trading")
    return {
        "shadow_run_report_path": str(run_path),
        "shadow_run_report_md_path": str(md_path),
        "shadow_orders_path": str(orders_path),
        "shadow_fills_path": str(fills_path),
        "shadow_positions_path": str(positions_path),
        "shadow_account_snapshots_path": str(snapshots_path),
        "shadow_drift_report_path": str(drift_path),
        "shadow_performance_report_path": str(performance_path),
        "shadow_vs_production_comparison_path": str(comparison_path),
    }


def _render_markdown(payload: dict) -> str:
    return "\n".join(
        [
            "# Shadow Trading Run",
            "",
            f"- production_run_id: `{payload.get('production_run_id')}`",
            f"- trade_date: `{payload.get('trade_date')}`",
            f"- status: `{payload.get('status')}`",
            f"- execution_mode: `{payload.get('execution_mode')}`",
            "",
            "## Summary",
            "",
            "```json",
            json.dumps(payload.get("summary", {}), ensure_ascii=False, indent=2),
            "```",
        ]
    ) + "\n"
