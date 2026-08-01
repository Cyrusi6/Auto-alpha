"""Local JSON/JSONL incident store."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import IncidentRecord, IncidentReport, IncidentRunbookStep, IncidentStatus


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class LocalIncidentStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.records_path = self.root_dir / "incident_records.jsonl"
        self.state_path = self.root_dir / "incident_state.json"
        self.events_path = self.root_dir / "incident_events.jsonl"
        self.runbook_path = self.root_dir / "incident_runbook.json"

    def make_incident_id(self, production_run_id: str | None, code: str, artifact_refs: dict[str, str] | None = None) -> str:
        payload = json.dumps(
            {
                "production_run_id": production_run_id or "",
                "code": code,
                "artifact_refs": artifact_refs or {},
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return f"inc_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"

    def save_incident(self, incident: IncidentRecord) -> IncidentRecord:
        existing = {item.incident_id: item for item in self.list_incidents()}
        if incident.incident_id in existing:
            return existing[incident.incident_id]
        incidents = list(existing.values()) + [incident]
        self._write_records(incidents)
        self._write_state(incidents)
        self._append_event("create", incident.incident_id, incident.status, {"code": incident.code, "severity": incident.severity})
        return incident

    def list_incidents(self, status: str | None = None) -> list[IncidentRecord]:
        records = []
        if not self.records_path.exists():
            return records
        for line in self.records_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            record = _incident_from_payload(payload)
            if status is None or record.status == status:
                records.append(record)
        return records

    def get_incident(self, incident_id: str) -> IncidentRecord | None:
        return next((item for item in self.list_incidents() if item.incident_id == incident_id), None)

    def update_status(self, incident_id: str, status: str, actor: str | None = None, comment: str | None = None) -> IncidentRecord:
        incidents = self.list_incidents()
        updated = None
        now = utc_now()
        next_records = []
        for item in incidents:
            if item.incident_id != incident_id:
                next_records.append(item)
                continue
            metadata = dict(item.metadata)
            if comment:
                metadata.setdefault("comments", []).append({"actor": actor, "comment": comment, "at": now})
            updated = replace(
                item,
                status=status,
                owner=actor or item.owner,
                acknowledged_at=now if status == IncidentStatus.acknowledged and not item.acknowledged_at else item.acknowledged_at,
                resolved_at=now if status in {IncidentStatus.resolved, IncidentStatus.suppressed} else item.resolved_at,
                metadata=metadata,
            )
            next_records.append(updated)
        if updated is None:
            raise FileNotFoundError(f"incident not found: {incident_id}")
        self._write_records(next_records)
        self._write_state(next_records)
        self._append_event(status, incident_id, status, {"actor": actor, "comment": comment})
        return updated

    def write_report(self, production_run_id: str | None = None, trade_date: str | None = None) -> IncidentReport:
        incidents = self.list_incidents()
        summary = summarize_incidents(incidents)
        report = IncidentReport(created_at=utc_now(), production_run_id=production_run_id, trade_date=trade_date, incidents=incidents, summary=summary)
        write_json_artifact(self.root_dir / "incident_report.json", report.to_dict(), "incident_report", "incident_response")
        write_jsonl_artifact(self.root_dir / "incident_records.jsonl", [item.to_dict() for item in incidents], "incident_records", "incident_response")
        write_jsonl_artifact(self.root_dir / "incident_events.jsonl", _read_jsonl(self.events_path), "incident_events", "incident_response")
        runbook = {"steps": [step.to_dict() for item in incidents for step in item.runbook_steps]}
        write_json_artifact(self.runbook_path, runbook, "incident_runbook", "incident_response")
        return report

    def _write_records(self, incidents: list[IncidentRecord]) -> None:
        write_jsonl_artifact(self.records_path, [item.to_dict() for item in incidents], "incident_records", "incident_response")

    def _write_state(self, incidents: list[IncidentRecord]) -> None:
        payload = {
            "updated_at": utc_now(),
            "open_count": sum(1 for item in incidents if item.status in {IncidentStatus.open, IncidentStatus.acknowledged, IncidentStatus.mitigated}),
            "critical_count": sum(1 for item in incidents if item.severity == "critical"),
            "incidents": {item.incident_id: {"status": item.status, "code": item.code, "severity": item.severity} for item in incidents},
        }
        write_json_artifact(self.state_path, payload, "incident_state", "incident_response")

    def _append_event(self, event: str, incident_id: str, status: str, metadata: dict[str, Any] | None = None) -> None:
        record = {"event_id": f"evt_{hashlib.sha256((incident_id + event + utc_now()).encode()).hexdigest()[:16]}", "event": event, "incident_id": incident_id, "status": status, "created_at": utc_now(), "metadata": metadata or {}}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def summarize_incidents(incidents: list[IncidentRecord]) -> dict[str, Any]:
    return {
        "incident_count": len(incidents),
        "open_count": sum(1 for item in incidents if item.status in {IncidentStatus.open, IncidentStatus.acknowledged, IncidentStatus.mitigated}),
        "critical_count": sum(1 for item in incidents if item.severity == "critical"),
        "error_count": sum(1 for item in incidents if item.severity == "error"),
        "warning_count": sum(1 for item in incidents if item.severity == "warning"),
        "resolved_count": sum(1 for item in incidents if item.status == IncidentStatus.resolved),
        "suppressed_count": sum(1 for item in incidents if item.status == IncidentStatus.suppressed),
    }


def _incident_from_payload(payload: dict[str, Any]) -> IncidentRecord:
    steps = [IncidentRunbookStep(**step) for step in payload.get("runbook_steps", [])]
    return IncidentRecord(
        incident_id=str(payload.get("incident_id")),
        production_run_id=payload.get("production_run_id"),
        trade_date=payload.get("trade_date"),
        severity=str(payload.get("severity", "warning")),
        status=str(payload.get("status", "open")),
        source=str(payload.get("source", "manual")),
        code=str(payload.get("code", "unknown")),
        title=str(payload.get("title", "")),
        description=str(payload.get("description", "")),
        created_at=str(payload.get("created_at", utc_now())),
        artifact_refs=dict(payload.get("artifact_refs") or {}),
        acknowledged_at=payload.get("acknowledged_at"),
        resolved_at=payload.get("resolved_at"),
        owner=payload.get("owner"),
        recommended_actions=list(payload.get("recommended_actions") or []),
        runbook_steps=steps,
        kill_switch_action=payload.get("kill_switch_action"),
        metadata=dict(payload.get("metadata") or {}),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
