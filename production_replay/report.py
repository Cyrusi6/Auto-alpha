"""Report writers for production replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import ProductionReplayPlan, ProductionReplayReport


def write_replay_plan(plan: ProductionReplayPlan, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    plan_path = write_json_artifact(root / "production_replay_plan.json", plan.to_dict(), "production_replay_plan", "production_replay")
    md_path = root / "production_replay_plan.md"
    md_path.write_text(
        "\n".join(
            [
                "# Production Replay Plan",
                "",
                f"- replay_id: `{plan.replay_id}`",
                f"- replay_name: `{plan.replay_name}`",
                f"- replay_mode: `{plan.replay_mode}`",
                f"- day_count: `{plan.day_count}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {"production_replay_plan_path": str(plan_path), "production_replay_plan_md_path": str(md_path)}


def write_replay_report(report: ProductionReplayReport, output_dir: str | Path, events: list[dict[str, Any]] | None = None) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    report_path = write_json_artifact(root / "production_replay_report.json", payload, "production_replay_report", "production_replay")
    md_path = root / "production_replay_report.md"
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    days_path = write_jsonl_artifact(
        root / "production_replay_days.jsonl",
        [day.to_dict() for day in report.day_results],
        "production_replay_days",
        "production_replay",
    )
    events_path = write_jsonl_artifact(root / "production_replay_events.jsonl", events or [], "production_replay_events", "production_replay")
    package = {
        "replay_id": report.replay_id,
        "status": report.status,
        "summary": report.summary,
        "paths": {
            "production_replay_report_path": str(report_path),
            "production_replay_report_md_path": str(md_path),
            "production_replay_days_path": str(days_path),
            "production_replay_events_path": str(events_path),
        },
    }
    package_path = write_json_artifact(root / "production_replay_package.json", package, "production_replay_package", "production_replay")
    catalog = {
        "replay_id": report.replay_id,
        "entries": [
            {"name": "production_replay_report", "path": str(report_path), "kind": "json", "stage": "replay"},
            {"name": "production_replay_days", "path": str(days_path), "kind": "jsonl", "stage": "replay"},
            {"name": "production_replay_events", "path": str(events_path), "kind": "jsonl", "stage": "replay"},
            {"name": "production_replay_package", "path": str(package_path), "kind": "json", "stage": "replay"},
        ],
    }
    catalog_path = write_json_artifact(
        root / "production_replay_artifact_catalog.json",
        catalog,
        "production_replay_artifact_catalog",
        "production_replay",
    )
    return {
        "production_replay_report_path": str(report_path),
        "production_replay_report_md_path": str(md_path),
        "production_replay_days_path": str(days_path),
        "production_replay_events_path": str(events_path),
        "production_replay_package_path": str(package_path),
        "production_replay_artifact_catalog_path": str(catalog_path),
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    return "\n".join(
        [
            "# Production Replay Report",
            "",
            f"- replay_id: `{payload.get('replay_id')}`",
            f"- replay_name: `{payload.get('replay_name')}`",
            f"- replay_mode: `{payload.get('replay_mode')}`",
            f"- status: `{payload.get('status')}`",
            f"- day_count: `{summary.get('replay_day_count', 0)}`",
            f"- failed_days: `{summary.get('replay_failed_day_count', 0)}`",
            f"- blocked_days: `{summary.get('replay_blocked_day_count', 0)}`",
            "",
            "## Summary",
            "",
            "```json",
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ]
    ) + "\n"
