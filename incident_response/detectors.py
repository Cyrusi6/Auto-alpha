"""Incident detectors for local production artifacts."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import IncidentRecord, IncidentSeverity, IncidentSource, IncidentStatus
from .runbook import build_runbook_steps
from .store import LocalIncidentStore


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def detect_incidents(
    store: LocalIncidentStore,
    production_run_id: str | None = None,
    trade_date: str | None = None,
    artifact_paths: dict[str, str | Path | None] | None = None,
) -> list[IncidentRecord]:
    artifacts = artifact_paths or {}
    detected: list[IncidentRecord] = []
    production = _read_json(artifacts.get("production_orchestrator_report_path"))
    monitoring = _read_json(artifacts.get("monitoring_report_path"))
    risk = _read_json(artifacts.get("risk_control_report_path"))
    eod = _read_json(artifacts.get("eod_reconciliation_report_path"))
    freeze = _read_json(artifacts.get("freeze_validation_report_path"))
    portfolio = _read_json(artifacts.get("portfolio_certification_decision_path"))

    if production:
        for phase in production.get("phase_runs", []):
            if phase.get("status") in {"failed", "blocked"}:
                detected.append(_make(store, production_run_id, trade_date, "production_phase_failure", "Production phase failed", str(phase.get("error") or phase.get("phase")), IncidentSeverity.error, IncidentSource.production_orchestrator, artifacts))
        for gate in production.get("gate_results", []):
            if gate.get("status") in {"blocked", "failed"}:
                detected.append(_make(store, production_run_id, trade_date, str(gate.get("gate_id") or "readiness_gate_blocked"), "Production readiness gate blocked", str(gate.get("reason") or ""), IncidentSeverity.error, IncidentSource.production_orchestrator, artifacts))
    if freeze and int(freeze.get("error_count", 0) or 0) > 0:
        detected.append(_make(store, production_run_id, trade_date, "freeze_hash_drift", "Data freeze validation failed", "Freeze validation produced errors.", IncidentSeverity.critical, IncidentSource.data_lake, artifacts))
    if portfolio and str(portfolio.get("status", "")) in {"rejected", "insufficient_data"}:
        detected.append(_make(store, production_run_id, trade_date, "portfolio_certification_blocked", "Portfolio certification blocked", str(portfolio.get("status")), IncidentSeverity.error, IncidentSource.portfolio_certification, artifacts))
    if risk and (str(risk.get("status", "")) in {"blocked", "failed"} or int(risk.get("rejected_orders", 0) or 0) > 0):
        detected.append(_make(store, production_run_id, trade_date, "risk_blocker", "Risk control blocked orders", "Risk control report contains blocker or rejected orders.", IncidentSeverity.error, IncidentSource.risk_controls, artifacts))
    if eod and (str(eod.get("status", "")) in {"error", "blocker"} or int((eod.get("summary") or {}).get("material_break_count", 0) or 0) > 0):
        detected.append(_make(store, production_run_id, trade_date, "eod_reconciliation_blocker", "EOD reconciliation blocker", "EOD reconciliation contains material breaks.", IncidentSeverity.error, IncidentSource.reconciliation_center, artifacts))
    for alert in monitoring.get("alerts", []) if monitoring else []:
        if alert.get("severity") in {"error", "critical"}:
            detected.append(_make(store, production_run_id, trade_date, "monitoring_error_alert", "Monitoring error alert", str(alert.get("message") or ""), str(alert.get("severity")), IncidentSource.monitoring, artifacts))
    saved = [store.save_incident(item) for item in detected]
    store.write_report(production_run_id=production_run_id, trade_date=trade_date)
    return saved


def _make(
    store: LocalIncidentStore,
    production_run_id: str | None,
    trade_date: str | None,
    code: str,
    title: str,
    description: str,
    severity: str,
    source: str,
    artifacts: dict[str, str | Path | None],
) -> IncidentRecord:
    refs = {key: str(value) for key, value in artifacts.items() if value}
    incident_id = store.make_incident_id(production_run_id, code, refs)
    return IncidentRecord(
        incident_id=incident_id,
        production_run_id=production_run_id,
        trade_date=trade_date,
        severity=severity,
        status=IncidentStatus.open,
        source=source,
        code=code,
        title=title,
        description=description,
        created_at=utc_now(),
        artifact_refs=refs,
        recommended_actions=["inspect_artifact", "stop_next_phase", "resume_after_validation"],
        runbook_steps=build_runbook_steps(code),
        kill_switch_action="review_activate" if severity == IncidentSeverity.critical else None,
    )


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
