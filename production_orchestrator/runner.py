"""Production orchestrator runner that reuses existing daily operations."""

from __future__ import annotations

import contextlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from incident_response import (
    IncidentRecord,
    IncidentSeverity,
    IncidentSource,
    IncidentStatus,
    LocalIncidentStore,
    detect_incidents,
    write_incident_report,
)
from incident_response.runbook import build_runbook_steps
from operations.run_daily import main as operations_main
from shadow_trading import run_shadow_trading

from .gates import evaluate_readiness_gates
from .models import (
    ProductionGateStatus,
    ProductionPhase,
    ProductionPhaseRun,
    ProductionPhaseStatus,
    ProductionReadinessReport,
    ProductionRunMode,
    ProductionRunRecord,
    ProductionRunReport,
)
from .planner import build_production_plan
from .report import write_production_plan, write_production_report
from .state import LocalProductionStateStore


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@dataclass
class ProductionOrchestratorConfig:
    production_state_dir: str
    output_dir: str
    run_mode: str
    trade_date: str
    as_of_date: str
    environment: str = "paper"
    production_run_id: str | None = None
    data_dir: str | None = None
    data_freeze_dir: str | None = None
    data_version_manifest_path: str | None = None
    require_data_freeze: bool = False
    factor_store_dir: str | None = None
    model_registry_dir: str | None = None
    require_active_model: bool = False
    require_active_optimizer_policy: bool = False
    certified_portfolio_policy_path: str | None = None
    portfolio_certification_decision_path: str | None = None
    require_certified_portfolio_policy: bool = False
    approval_store_dir: str | None = None
    paper_account_dir: str | None = None
    orders_dir: str | None = None
    shadow_dir: str | None = None
    settlement_dir: str | None = None
    broker_store_dir: str | None = None
    broker_adapter: str = "paper"
    broker_file_gateway: bool = False
    broker_file_profile: str = "generic_broker_csv"
    broker_file_profile_config: str | None = None
    broker_file_gateway_store_dir: str | None = None
    broker_file_outbox_dir: str | None = None
    broker_file_inbox_dir: str | None = None
    broker_file_handoff_dir: str | None = None
    operator_handoff_store_dir: str | None = None
    operator_handoff_approval_store_dir: str | None = None
    mapping_certification_decision_path: str | None = None
    require_mapping_certification: bool = False
    file_outbox_dry_run: bool = False
    auto_confirm_local_smoke: bool = False
    broker_statement_dir: str | None = None
    eod_reconciliation_dir: str | None = None
    risk_control_state_dir: str | None = None
    risk_control_output_dir: str | None = None
    risk_policy_path: str | None = None
    incident_store_dir: str | None = None
    monitoring_dir: str | None = None
    portfolio_value: float = 1_000_000.0
    index_code: str = "000300.SH"
    top_n: int = 20
    max_weight: float = 0.10
    capacity_aware: bool = False
    point_in_time: bool = False
    feature_cutoff_mode: str = "same_day_after_close"
    corporate_action_aware: bool = False
    apply_corporate_actions: bool = False
    corporate_action_dir: str | None = None
    target_return_mode: str = "adjusted_close"
    settlement_aware: bool = False
    risk_controls: bool = False
    require_order_approval: bool = False
    approval_id: str | None = None
    stop_after_phase: str | None = None
    start_at_phase: str | None = None
    resume: bool = False
    fail_on_blocker: bool = False
    continue_on_warning: bool = False
    pretty: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class ProductionOrchestratorRunner:
    def __init__(self, config: ProductionOrchestratorConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.state = LocalProductionStateStore(config.production_state_dir)
        self.incidents = LocalIncidentStore(config.incident_store_dir or str(self.output_dir / "incidents"))
        self.plan = build_production_plan(config.trade_date, config.as_of_date, config.run_mode, config.environment, config.production_run_id)
        self.production_run_id = self.plan.production_run_id
        self.phase_runs: list[ProductionPhaseRun] = []
        self.artifact_paths: dict[str, str] = {}
        self.readiness = self._readiness()

    def plan_day(self) -> dict[str, Any]:
        plan_paths = write_production_plan(self.plan, self.output_dir)
        self.artifact_paths.update(plan_paths)
        self._record_run(status="planned", current_phase=ProductionPhase.plan_day)
        report = self._build_report("planned")
        report_paths = write_production_report(report, self.readiness, self.output_dir)
        self.artifact_paths.update(report_paths)
        return {"status": "planned", "production_run_id": self.production_run_id, "run_mode": self.config.run_mode, "paths": {**plan_paths, **report_paths}, "readiness": self.readiness.to_dict()}

    def run_day(self, close_only: bool = False) -> dict[str, Any]:
        self.plan_day()
        if self._blocked():
            incident_paths = self._detect_incidents()
            report = self._build_report("blocked", incident_paths)
            paths = write_production_report(report, self.readiness, self.output_dir)
            return {"status": "blocked", "production_run_id": self.production_run_id, "paths": {**self.artifact_paths, **paths, **incident_paths}, "readiness": self.readiness.to_dict()}
        if close_only:
            return self.close_day()
        if self.config.run_mode == ProductionRunMode.shadow_only:
            self._phase_operations_propose()
            if self.config.stop_after_phase == ProductionPhase.wait_for_approval:
                return self._finish("waiting_approval")
            self._phase_shadow_execute()
            return self._finish("success")
        if self.config.run_mode in {ProductionRunMode.paper_simulated, ProductionRunMode.file_outbox}:
            if self.config.approval_id:
                self._phase_execute_approved()
                if self.config.run_mode == ProductionRunMode.file_outbox:
                    self._record_file_outbox_phases()
                return self._finish("success")
            self._phase_operations_propose()
            if self.config.stop_after_phase == ProductionPhase.wait_for_approval or self.config.require_order_approval:
                return self._finish("waiting_approval")
            return self._finish("success")
        return self._finish("success")

    def close_day(self) -> dict[str, Any]:
        self._record_phase(ProductionPhase.close_day, ProductionPhaseStatus.success, {"close_day_status": "closed"})
        return self._finish("closed")

    def _phase_operations_propose(self) -> dict[str, Any]:
        output_dir = self.output_dir / "operations_propose"
        argv = self._operations_base_args(output_dir, execute=False)
        argv.append("--require-approval")
        payload, stdout_tail = self._call_json("operations_propose", operations_main, argv)
        status = ProductionPhaseStatus.waiting_approval if payload.get("approval_id") else ProductionPhaseStatus.success
        self.artifact_paths.update(_extract_paths(payload))
        self._record_phase(ProductionPhase.generate_orders, status, payload, stdout_tail=stdout_tail)
        if payload.get("approval_id"):
            self._record_phase(ProductionPhase.wait_for_approval, ProductionPhaseStatus.waiting_approval, {"approval_id": payload.get("approval_id")})
        return payload

    def _phase_execute_approved(self) -> dict[str, Any]:
        output_dir = self.output_dir / "operations_execute"
        argv = self._operations_base_args(output_dir, execute=True)
        argv.extend(["--approval-id", str(self.config.approval_id), "--execute-approved"])
        if self.config.broker_adapter:
            argv.extend(["--broker-adapter", self.config.broker_adapter])
        if self.config.broker_store_dir:
            argv.extend(["--broker-store-dir", self.config.broker_store_dir, "--broker-reconcile"])
        payload, stdout_tail = self._call_json("operations_execute", operations_main, argv)
        self.artifact_paths.update(_extract_paths(payload))
        self._record_phase(ProductionPhase.execute_approved, ProductionPhaseStatus.success if payload.get("status") != "failed" else ProductionPhaseStatus.failed, payload, error=payload.get("error"), stdout_tail=stdout_tail)
        return payload

    def _phase_shadow_execute(self) -> dict[str, Any]:
        shadow_dir = self.config.shadow_dir or str(self.output_dir / "shadow")
        report = run_shadow_trading(
            production_run_id=self.production_run_id,
            trade_date=self.config.trade_date,
            as_of_date=self.config.as_of_date,
            orders_dir=self.config.orders_dir or str(self.output_dir / "orders"),
            execution_plan_dir=str(Path(self.config.orders_dir or self.output_dir / "orders") / "plan"),
            output_dir=shadow_dir,
        )
        self.artifact_paths.update(report.paths)
        self._record_phase(ProductionPhase.shadow_execute, ProductionPhaseStatus.success, report.summary)
        return report.to_dict()

    def _operations_base_args(self, output_dir: Path, execute: bool) -> list[str]:
        if not self.config.factor_store_dir or not self.config.approval_store_dir or not self.config.paper_account_dir:
            raise ValueError("factor_store_dir, approval_store_dir and paper_account_dir are required for operations phases")
        orders_dir = self.config.orders_dir or str(self.output_dir / "orders")
        argv = [
            "--data-dir",
            self.config.data_dir or "",
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--approval-store-dir",
            self.config.approval_store_dir,
            "--paper-account-dir",
            self.config.paper_account_dir,
            "--output-dir",
            str(output_dir),
            "--orders-dir",
            orders_dir,
            "--rebalance-date",
            self.config.trade_date,
            "--portfolio-value",
            str(self.config.portfolio_value),
            "--index-code",
            self.config.index_code,
            "--top-n",
            str(self.config.top_n),
            "--max-weight",
            str(self.config.max_weight),
            "--production-run-id",
            self.production_run_id,
            "--production-state-dir",
            self.config.production_state_dir,
            "--run-mode",
            self.config.run_mode,
        ]
        if self.config.data_freeze_dir:
            argv.extend(["--data-freeze-dir", self.config.data_freeze_dir])
        if self.config.data_version_manifest_path:
            argv.extend(["--data-version-manifest-path", self.config.data_version_manifest_path])
        if self.config.require_data_freeze:
            argv.append("--require-data-freeze")
        if self.config.model_registry_dir:
            argv.extend(["--use-model-registry", "--model-registry-dir", self.config.model_registry_dir])
        if self.config.require_active_model:
            argv.append("--require-active-model")
        if self.config.require_active_optimizer_policy:
            argv.extend(["--active-optimizer-policy", "--require-active-optimizer-policy"])
        if self.config.certified_portfolio_policy_path:
            argv.extend(["--certified-portfolio-policy-path", self.config.certified_portfolio_policy_path])
        if self.config.portfolio_certification_decision_path:
            argv.extend(["--portfolio-certification-decision-path", self.config.portfolio_certification_decision_path])
        if self.config.require_certified_portfolio_policy:
            argv.append("--require-certified-portfolio-policy")
        if self.config.capacity_aware:
            argv.extend(["--capacity-aware", "--execution-plan-dir", str(Path(orders_dir) / "plan")])
        if self.config.point_in_time:
            argv.extend(["--point-in-time", "--feature-cutoff-mode", self.config.feature_cutoff_mode])
        if self.config.corporate_action_aware:
            argv.append("--corporate-action-aware")
        if self.config.apply_corporate_actions:
            argv.append("--apply-corporate-actions")
        if self.config.corporate_action_dir:
            argv.extend(["--corporate-action-dir", self.config.corporate_action_dir])
        if self.config.target_return_mode:
            argv.extend(["--target-return-mode", self.config.target_return_mode])
        if self.config.settlement_aware:
            argv.extend(["--settlement-aware", "--settlement-dir", self.config.settlement_dir or str(self.output_dir / "settlement")])
        if self.config.risk_controls:
            argv.append("--risk-controls")
            if self.config.risk_control_state_dir:
                argv.extend(["--risk-control-state-dir", self.config.risk_control_state_dir])
            if self.config.risk_control_output_dir:
                argv.extend(["--risk-control-output-dir", self.config.risk_control_output_dir])
            argv.append("--block-on-kill-switch")
        if execute and self.config.broker_adapter == "simulated":
            argv.append("--broker-auto-fill")
        if execute and self.config.broker_file_gateway:
            argv.append("--broker-file-gateway")
            argv.extend(["--broker-file-profile", self.config.broker_file_profile])
            if self.config.broker_file_profile_config:
                argv.extend(["--broker-file-profile-config", self.config.broker_file_profile_config])
            if self.config.broker_file_gateway_store_dir:
                argv.extend(["--broker-file-gateway-store-dir", self.config.broker_file_gateway_store_dir])
            if self.config.broker_file_outbox_dir:
                argv.extend(["--broker-file-outbox-dir", self.config.broker_file_outbox_dir])
            if self.config.broker_file_inbox_dir:
                argv.extend(["--broker-file-inbox-dir", self.config.broker_file_inbox_dir])
            if self.config.broker_file_handoff_dir:
                argv.extend(["--broker-file-handoff-dir", self.config.broker_file_handoff_dir])
            if self.config.operator_handoff_store_dir:
                argv.extend(["--operator-handoff-store-dir", self.config.operator_handoff_store_dir])
            if self.config.operator_handoff_approval_store_dir:
                argv.extend(["--operator-handoff-approval-store-dir", self.config.operator_handoff_approval_store_dir])
            if self.config.mapping_certification_decision_path:
                argv.extend(["--mapping-certification-decision-path", self.config.mapping_certification_decision_path])
            if self.config.require_mapping_certification:
                argv.append("--require-mapping-certification")
            if self.config.file_outbox_dry_run:
                argv.append("--file-outbox-dry-run")
            if self.config.auto_confirm_local_smoke:
                argv.append("--auto-confirm-local-smoke")
        return [item for item in argv if item != ""]

    def _record_file_outbox_phases(self) -> None:
        latest = self.phase_runs[-1].summary if self.phase_runs else {}
        summary = latest.get("summary", latest) if isinstance(latest, dict) else {}
        broker_summary = summary.get("broker_summary", {}) if isinstance(summary, dict) else {}
        for phase in [
            ProductionPhase.mapping_certification_check,
            ProductionPhase.export_broker_files,
            ProductionPhase.create_operator_handoff,
            ProductionPhase.wait_handoff_approval,
            ProductionPhase.import_broker_file_inbox,
            ProductionPhase.broker_file_roundtrip_check,
        ]:
            status = ProductionPhaseStatus.success
            if phase == ProductionPhase.wait_handoff_approval and broker_summary.get("operator_handoff_missing_required_items"):
                status = ProductionPhaseStatus.waiting_approval
            self._record_phase(phase, status, summary)

    def _readiness(self) -> ProductionReadinessReport:
        readiness = evaluate_readiness_gates(
            production_run_id=self.production_run_id,
            trade_date=self.config.trade_date,
            as_of_date=self.config.as_of_date,
            data_dir=self.config.data_dir,
            data_freeze_dir=self.config.data_freeze_dir,
            require_data_freeze=self.config.require_data_freeze,
            model_registry_dir=self.config.model_registry_dir,
            require_active_model=self.config.require_active_model,
            require_active_optimizer_policy=self.config.require_active_optimizer_policy,
            certified_portfolio_policy_path=self.config.certified_portfolio_policy_path,
            portfolio_certification_decision_path=self.config.portfolio_certification_decision_path,
            require_certified_portfolio_policy=self.config.require_certified_portfolio_policy,
            paper_account_dir=self.config.paper_account_dir,
            risk_control_state_dir=self.config.risk_control_state_dir,
            risk_controls=self.config.risk_controls,
        )
        for gate in readiness.gates:
            self.state.record_gate(self.production_run_id, gate)
        return readiness

    def _blocked(self) -> bool:
        return any(gate.status in {ProductionGateStatus.blocked, ProductionGateStatus.failed} for gate in self.readiness.gates)

    def _detect_incidents(self) -> dict[str, str]:
        self._create_gate_incidents()
        paths = {
            "production_orchestrator_report_path": str(self.output_dir / "production_orchestrator_report.json"),
            "portfolio_certification_decision_path": self.config.portfolio_certification_decision_path,
        }
        detect_incidents(self.incidents, self.production_run_id, self.config.trade_date, paths)
        return write_incident_report(self.incidents, production_run_id=self.production_run_id, trade_date=self.config.trade_date)

    def _create_gate_incidents(self) -> None:
        for gate in self.readiness.gates:
            if gate.status not in {ProductionGateStatus.blocked, ProductionGateStatus.failed}:
                continue
            artifact_refs = {key: str(value) for key, value in gate.artifact_refs.items() if value}
            incident_id = self.incidents.make_incident_id(self.production_run_id, gate.gate_id, artifact_refs)
            self.incidents.save_incident(
                IncidentRecord(
                    incident_id=incident_id,
                    production_run_id=self.production_run_id,
                    trade_date=self.config.trade_date,
                    severity=IncidentSeverity.error,
                    status=IncidentStatus.open,
                    source=IncidentSource.production_orchestrator,
                    code=gate.gate_id,
                    title="Production readiness gate blocked",
                    description=gate.reason,
                    created_at=utc_now(),
                    artifact_refs=artifact_refs,
                    recommended_actions=[gate.recommended_action or "review_gate_and_resume"],
                    runbook_steps=build_runbook_steps(gate.gate_id),
                    kill_switch_action="review_activate" if gate.severity == "critical" else None,
                    metadata={"value": gate.value, "threshold": gate.threshold},
                )
            )

    def _finish(self, status: str) -> dict[str, Any]:
        incident_paths = self._detect_incidents()
        report = self._build_report(status, incident_paths)
        paths = write_production_report(report, self.readiness, self.output_dir)
        self.artifact_paths.update(paths)
        self.artifact_paths.update(incident_paths)
        self._record_run(status=status, current_phase=ProductionPhase.publish_report)
        return {"status": status, "production_run_id": self.production_run_id, "run_mode": self.config.run_mode, "paths": self.artifact_paths, "summary": report.summary, "readiness": self.readiness.to_dict()}

    def _build_report(self, status: str, incident_paths: dict[str, str] | None = None) -> ProductionRunReport:
        incident_summary = {}
        if incident_paths and Path(incident_paths.get("incident_report_path", "")).exists():
            incident_summary = json.loads(Path(incident_paths["incident_report_path"]).read_text(encoding="utf-8")).get("summary", {})
        summary = {
            "phase_count": len(self.plan.phases),
            "phase_failed_count": sum(1 for phase in self.phase_runs if phase.status == ProductionPhaseStatus.failed),
            "phase_blocked_count": sum(1 for phase in self.phase_runs if phase.status == ProductionPhaseStatus.blocked),
            "gate_blocker_count": self.readiness.summary.get("blocker_count", 0),
            "gate_warning_count": self.readiness.summary.get("warning_count", 0),
            "approval_id": self._latest_approval_id(),
            "close_day_status": "closed" if status == "closed" else "",
        }
        return ProductionRunReport(
            production_run_id=self.production_run_id,
            trade_date=self.config.trade_date,
            as_of_date=self.config.as_of_date,
            run_mode=self.config.run_mode,
            environment=self.config.environment,
            status=status,
            plan=self.plan.to_dict(),
            readiness=self.readiness.to_dict(),
            phase_runs=self.phase_runs,
            gate_results=self.readiness.gates,
            artifact_paths=self.artifact_paths,
            incident_summary=incident_summary,
            summary=summary,
        )

    def _record_phase(self, phase: str, status: str, summary: dict[str, Any], error: str | None = None, stdout_tail: str = "") -> None:
        record = ProductionPhaseRun(
            production_run_id=self.production_run_id,
            phase=phase,
            status=status,
            started_at=utc_now(),
            finished_at=utc_now(),
            output_paths=_extract_paths(summary),
            summary=summary,
            error=error,
            stdout_tail=stdout_tail[-2000:],
        )
        self.phase_runs = [item for item in self.phase_runs if item.phase != phase] + [record]
        self.state.record_phase(record)

    def _record_run(self, status: str, current_phase: str | None) -> None:
        record = ProductionRunRecord(
            production_run_id=self.production_run_id,
            trade_date=self.config.trade_date,
            as_of_date=self.config.as_of_date,
            run_mode=self.config.run_mode,
            environment=self.config.environment,
            status=status,
            created_at=utc_now(),
            updated_at=utc_now(),
            current_phase=current_phase,
            phase_statuses={phase.phase: phase.status for phase in self.phase_runs},
            artifact_paths=self.artifact_paths,
            gate_summary=self.readiness.summary,
            metadata={"output_dir": str(self.output_dir)},
        )
        self.state.save_run(record)

    def _call_json(self, name: str, func, argv: list[str]) -> tuple[dict[str, Any], str]:
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = func(argv)
            output = stdout.getvalue()
            payload = json.loads(output) if output.strip() else {}
            if code != 0:
                payload.setdefault("status", "failed")
                payload.setdefault("error", f"{name} exited with {code}")
            return payload, output[-2000:]
        except Exception as exc:
            output = stdout.getvalue()
            return {"status": "failed", "error": str(exc)}, output[-2000:]

    def _latest_approval_id(self) -> str | None:
        for phase in reversed(self.phase_runs):
            approval = phase.summary.get("approval_id") if isinstance(phase.summary, dict) else None
            if approval:
                return str(approval)
        return self.config.approval_id


def _extract_paths(payload: dict[str, Any]) -> dict[str, str]:
    paths = dict(payload.get("paths") or {}) if isinstance(payload.get("paths"), dict) else {}
    for key, value in payload.get("summary", {}).items() if isinstance(payload.get("summary"), dict) else []:
        if key.endswith("_path") and value:
            paths[key] = str(value)
    for key, value in payload.items():
        if key.endswith("_path") and value:
            paths[key] = str(value)
    return paths
