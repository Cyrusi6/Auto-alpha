"""Dataclasses for local production orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ProductionRunMode:
    dry_run = "dry_run"
    shadow_only = "shadow_only"
    paper_simulated = "paper_simulated"
    file_outbox = "file_outbox"


class ProductionPhase:
    plan_day = "plan_day"
    validate_data_freeze = "validate_data_freeze"
    validate_market_calendar = "validate_market_calendar"
    validate_active_model = "validate_active_model"
    validate_active_optimizer_policy = "validate_active_optimizer_policy"
    validate_certification = "validate_certification"
    validate_account_state = "validate_account_state"
    validate_risk_state = "validate_risk_state"
    apply_corporate_actions = "apply_corporate_actions"
    settle_before_trading = "settle_before_trading"
    generate_orders = "generate_orders"
    pre_trade_risk_gate = "pre_trade_risk_gate"
    create_order_approval = "create_order_approval"
    wait_for_approval = "wait_for_approval"
    mapping_certification_check = "mapping_certification_check"
    export_broker_files = "export_broker_files"
    create_operator_handoff = "create_operator_handoff"
    wait_handoff_approval = "wait_handoff_approval"
    import_broker_file_inbox = "import_broker_file_inbox"
    broker_file_roundtrip_check = "broker_file_roundtrip_check"
    broker_connectivity_probe = "broker_connectivity_probe"
    broker_readonly_snapshot = "broker_readonly_snapshot"
    broker_readonly_reconciliation = "broker_readonly_reconciliation"
    execute_approved = "execute_approved"
    shadow_execute = "shadow_execute"
    settle_after_execution = "settle_after_execution"
    import_broker_statement = "import_broker_statement"
    eod_reconciliation = "eod_reconciliation"
    monitoring = "monitoring"
    close_day = "close_day"
    publish_report = "publish_report"


class ProductionPhaseStatus:
    pending = "pending"
    running = "running"
    success = "success"
    warning = "warning"
    blocked = "blocked"
    failed = "failed"
    skipped = "skipped"
    waiting_approval = "waiting_approval"
    resumed = "resumed"


class ProductionGateStatus:
    passed = "passed"
    warning = "warning"
    blocked = "blocked"
    failed = "failed"
    skipped = "skipped"


@dataclass(frozen=True)
class ProductionGateResult:
    gate_id: str
    status: str
    severity: str
    reason: str
    value: Any = None
    threshold: Any = None
    artifact_refs: dict[str, str] = field(default_factory=dict)
    recommended_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionPhaseRun:
    production_run_id: str
    phase: str
    status: str
    started_at: str
    finished_at: str | None = None
    output_paths: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionRunPlan:
    production_run_id: str
    trade_date: str
    as_of_date: str
    run_mode: str
    environment: str
    phases: list[str]
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionReadinessReport:
    production_run_id: str
    trade_date: str
    as_of_date: str
    status: str
    gates: list[ProductionGateResult]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "production_run_id": self.production_run_id,
            "trade_date": self.trade_date,
            "as_of_date": self.as_of_date,
            "status": self.status,
            "gates": [gate.to_dict() for gate in self.gates],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class ProductionRunRecord:
    production_run_id: str
    trade_date: str
    as_of_date: str
    run_mode: str
    environment: str = "paper"
    data_freeze_id: str | None = None
    dataset_version_id: str | None = None
    model_version_id: str | None = None
    optimizer_policy_model_version_id: str | None = None
    portfolio_policy_id: str | None = None
    account_id: str = "paper_ashare"
    status: str = ProductionPhaseStatus.pending
    created_at: str = ""
    updated_at: str = ""
    current_phase: str | None = None
    phase_statuses: dict[str, str] = field(default_factory=dict)
    artifact_paths: dict[str, str] = field(default_factory=dict)
    gate_summary: dict[str, Any] = field(default_factory=dict)
    incident_summary: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionRunReport:
    production_run_id: str
    trade_date: str
    as_of_date: str
    run_mode: str
    environment: str
    status: str
    plan: dict[str, Any]
    readiness: dict[str, Any]
    phase_runs: list[ProductionPhaseRun]
    gate_results: list[ProductionGateResult]
    artifact_paths: dict[str, str]
    incident_summary: dict[str, Any]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "production_run_id": self.production_run_id,
            "trade_date": self.trade_date,
            "as_of_date": self.as_of_date,
            "run_mode": self.run_mode,
            "environment": self.environment,
            "status": self.status,
            "plan": self.plan,
            "readiness": self.readiness,
            "phase_runs": [phase.to_dict() for phase in self.phase_runs],
            "gate_results": [gate.to_dict() for gate in self.gate_results],
            "artifact_paths": dict(self.artifact_paths),
            "incident_summary": dict(self.incident_summary),
            "summary": dict(self.summary),
        }
