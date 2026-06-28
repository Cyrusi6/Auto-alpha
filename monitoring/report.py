"""Monitoring report writers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import MonitoringAlert, MonitoringReport


def build_monitoring_report(as_of_date: str, checks: dict, alerts: list[MonitoringAlert]) -> MonitoringReport:
    return MonitoringReport(
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        as_of_date=as_of_date,
        checks=checks,
        alerts=alerts,
    )


def write_monitoring_report(report: MonitoringReport, output_dir: str | Path) -> tuple[Path, Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "monitoring_report.json"
    md_path = root / "monitoring_report.md"
    alerts_path = root / "alerts.jsonl"
    payload = report.to_dict()
    write_json_artifact(json_path, payload, artifact_type="monitoring_report", producer="monitoring")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    write_jsonl_artifact(alerts_path, payload["alerts"], artifact_type="alerts", producer="monitoring")
    return json_path, md_path, alerts_path


def _render_markdown(payload: dict) -> str:
    lines = [
        "# Monitoring Report",
        "",
        f"- as_of_date: `{payload.get('as_of_date')}`",
        f"- alerts: {len(payload.get('alerts', []))}",
        "",
        "## Alerts",
        "",
        "| severity | check | message |",
        "| --- | --- | --- |",
    ]
    for alert in payload.get("alerts", []):
        lines.append(f"| {alert.get('severity')} | {alert.get('check')} | {alert.get('message')} |")
    lines.extend(["", "## Checks", "", "```json", json.dumps(payload.get("checks", {}), ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines) + "\n"
