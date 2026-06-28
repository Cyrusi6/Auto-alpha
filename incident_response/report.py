"""Incident report writers."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import IncidentReport
from .store import LocalIncidentStore


def write_incident_report(store: LocalIncidentStore, output_dir: str | Path | None = None, production_run_id: str | None = None, trade_date: str | None = None) -> dict[str, str]:
    report = store.write_report(production_run_id=production_run_id, trade_date=trade_date)
    root = Path(output_dir) if output_dir is not None else store.root_dir
    root.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    json_path = write_json_artifact(root / "incident_report.json", payload, "incident_report", "incident_response")
    md_path = root / "incident_report.md"
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    records_path = write_jsonl_artifact(root / "incident_records.jsonl", payload["incidents"], "incident_records", "incident_response")
    events_path = root / "incident_events.jsonl"
    if store.events_path.exists():
        events_path.write_text(store.events_path.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        write_jsonl_artifact(events_path, [], "incident_events", "incident_response")
    runbook_path = write_json_artifact(root / "incident_runbook.json", {"steps": [step for item in payload["incidents"] for step in item.get("runbook_steps", [])]}, "incident_runbook", "incident_response")
    return {
        "incident_report_path": str(json_path),
        "incident_report_md_path": str(md_path),
        "incident_records_path": str(records_path),
        "incident_events_path": str(events_path),
        "incident_runbook_path": str(runbook_path),
    }


def _render_markdown(payload: dict) -> str:
    lines = [
        "# Incident Report",
        "",
        f"- production_run_id: `{payload.get('production_run_id')}`",
        f"- trade_date: `{payload.get('trade_date')}`",
        f"- incidents: {len(payload.get('incidents', []))}",
        "",
        "| severity | status | code | title |",
        "| --- | --- | --- | --- |",
    ]
    for item in payload.get("incidents", []):
        lines.append(f"| {item.get('severity')} | {item.get('status')} | {item.get('code')} | {item.get('title')} |")
    lines.extend(["", "## Summary", "", "```json", json.dumps(payload.get("summary", {}), ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines) + "\n"
