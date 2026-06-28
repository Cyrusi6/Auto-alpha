"""Local state store for production orchestration."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import ProductionGateResult, ProductionPhaseRun, ProductionRunRecord


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class LocalProductionStateStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.runs_path = self.root_dir / "production_runs.jsonl"
        self.state_path = self.root_dir / "production_run_state.json"
        self.phase_runs_path = self.root_dir / "production_phase_runs.jsonl"
        self.gate_results_path = self.root_dir / "production_gate_results.jsonl"
        self.events_path = self.root_dir / "production_run_events.jsonl"
        self.runbook_path = self.root_dir / "production_runbook.json"

    def save_run(self, record: ProductionRunRecord) -> ProductionRunRecord:
        records = {item.production_run_id: item for item in self.list_runs()}
        records[record.production_run_id] = record
        write_jsonl_artifact(self.runs_path, [item.to_dict() for item in records.values()], "production_runs", "production_orchestrator")
        write_json_artifact(self.state_path, record.to_dict(), "production_run_state", "production_orchestrator")
        self.append_event(record.production_run_id, "save_run", record.status, {"current_phase": record.current_phase})
        return record

    def list_runs(self) -> list[ProductionRunRecord]:
        if not self.runs_path.exists():
            return []
        return [_run_from_payload(json.loads(line)) for line in self.runs_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def get_run(self, production_run_id: str) -> ProductionRunRecord | None:
        return next((item for item in self.list_runs() if item.production_run_id == production_run_id), None)

    def record_phase(self, phase: ProductionPhaseRun) -> None:
        phases = [item for item in self.list_phase_runs() if not (item.production_run_id == phase.production_run_id and item.phase == phase.phase)]
        phases.append(phase)
        write_jsonl_artifact(self.phase_runs_path, [item.to_dict() for item in phases], "production_phase_runs", "production_orchestrator")
        self.append_event(phase.production_run_id, "phase", phase.status, {"phase": phase.phase, "error": phase.error})

    def list_phase_runs(self, production_run_id: str | None = None) -> list[ProductionPhaseRun]:
        if not self.phase_runs_path.exists():
            return []
        rows = [_phase_from_payload(json.loads(line)) for line in self.phase_runs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [row for row in rows if production_run_id is None or row.production_run_id == production_run_id]

    def record_gate(self, production_run_id: str, gate: ProductionGateResult) -> None:
        rows = _read_jsonl(self.gate_results_path)
        rows = [row for row in rows if not (row.get("production_run_id") == production_run_id and row.get("gate_id") == gate.gate_id)]
        rows.append({"production_run_id": production_run_id, **gate.to_dict()})
        write_jsonl_artifact(self.gate_results_path, rows, "production_gate_results", "production_orchestrator")

    def append_event(self, production_run_id: str, event: str, status: str, metadata: dict[str, Any] | None = None) -> None:
        record = {"production_run_id": production_run_id, "event": event, "status": status, "created_at": utc_now(), "metadata": metadata or {}}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def write_runbook(self, production_run_id: str, steps: list[dict[str, Any]]) -> Path:
        return write_json_artifact(self.runbook_path, {"production_run_id": production_run_id, "steps": steps}, "production_runbook", "production_orchestrator")


def _run_from_payload(payload: dict[str, Any]) -> ProductionRunRecord:
    return ProductionRunRecord(**{field: payload.get(field) for field in ProductionRunRecord.__dataclass_fields__})


def _phase_from_payload(payload: dict[str, Any]) -> ProductionPhaseRun:
    return ProductionPhaseRun(**{field: payload.get(field) for field in ProductionPhaseRun.__dataclass_fields__})


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
