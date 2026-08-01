"""Incident response dataclasses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class IncidentSeverity:
    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


class IncidentStatus:
    open = "open"
    acknowledged = "acknowledged"
    mitigated = "mitigated"
    resolved = "resolved"
    suppressed = "suppressed"


class IncidentSource:
    production_orchestrator = "production_orchestrator"
    data_lake = "data_lake"
    model_registry = "model_registry"
    portfolio_certification = "portfolio_certification"
    risk_controls = "risk_controls"
    settlement_engine = "settlement_engine"
    broker_statement = "broker_statement"
    reconciliation_center = "reconciliation_center"
    monitoring = "monitoring"
    manual = "manual"


@dataclass(frozen=True)
class IncidentRunbookStep:
    step_id: str
    title: str
    action: str
    status: str = "pending"
    artifact_refs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IncidentRecord:
    incident_id: str
    production_run_id: str | None
    trade_date: str | None
    severity: str
    status: str
    source: str
    code: str
    title: str
    description: str
    created_at: str
    artifact_refs: dict[str, str] = field(default_factory=dict)
    acknowledged_at: str | None = None
    resolved_at: str | None = None
    owner: str | None = None
    recommended_actions: list[str] = field(default_factory=list)
    runbook_steps: list[IncidentRunbookStep] = field(default_factory=list)
    kill_switch_action: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["runbook_steps"] = [step.to_dict() if hasattr(step, "to_dict") else dict(step) for step in self.runbook_steps]
        return payload


@dataclass(frozen=True)
class IncidentReport:
    created_at: str
    production_run_id: str | None
    trade_date: str | None
    incidents: list[IncidentRecord]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "production_run_id": self.production_run_id,
            "trade_date": self.trade_date,
            "incidents": [item.to_dict() for item in self.incidents],
            "summary": dict(self.summary),
        }
