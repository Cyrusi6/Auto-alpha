"""Readiness gate checks for local production runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_registry import LocalModelRegistry
from risk_controls import LocalRiskControlState

from .calendar import ProductionCalendar
from .models import ProductionGateResult, ProductionGateStatus, ProductionReadinessReport


def evaluate_readiness_gates(
    *,
    production_run_id: str,
    trade_date: str,
    as_of_date: str,
    data_dir: str | Path | None = None,
    data_freeze_dir: str | Path | None = None,
    require_data_freeze: bool = False,
    model_registry_dir: str | Path | None = None,
    require_active_model: bool = False,
    require_active_optimizer_policy: bool = False,
    certified_portfolio_policy_path: str | Path | None = None,
    portfolio_certification_decision_path: str | Path | None = None,
    require_certified_portfolio_policy: bool = False,
    paper_account_dir: str | Path | None = None,
    risk_control_state_dir: str | Path | None = None,
    risk_controls: bool = False,
    compliance_pack_path: str | Path | None = None,
    broker_uat_report_path: str | Path | None = None,
    go_live_gate_decision_path: str | Path | None = None,
    require_go_live_gate: bool = False,
) -> ProductionReadinessReport:
    gates: list[ProductionGateResult] = []
    actual_data_dir = Path(data_freeze_dir) / "data" if data_freeze_dir else Path(data_dir) if data_dir else None
    gates.append(_gate_data_freeze(data_freeze_dir, require_data_freeze))
    gates.append(_gate_calendar(actual_data_dir, trade_date))
    gates.extend(_gate_model_registry(model_registry_dir, require_active_model, require_active_optimizer_policy))
    gates.append(
        _gate_portfolio_certification(
            certified_portfolio_policy_path,
            portfolio_certification_decision_path,
            require_certified_portfolio_policy,
            model_registry_dir=model_registry_dir,
        )
    )
    gates.append(_gate_account(paper_account_dir))
    if risk_controls:
        gates.append(_gate_risk_state(risk_control_state_dir))
    else:
        gates.append(ProductionGateResult("risk_state", ProductionGateStatus.skipped, "info", "risk controls disabled"))
    gates.append(_gate_go_live(go_live_gate_decision_path, require_go_live_gate, compliance_pack_path, broker_uat_report_path))
    blocked = sum(1 for gate in gates if gate.status in {ProductionGateStatus.blocked, ProductionGateStatus.failed})
    warnings = sum(1 for gate in gates if gate.status == ProductionGateStatus.warning)
    status = "blocked" if blocked else "warning" if warnings else "passed"
    summary = {
        "gate_count": len(gates),
        "blocker_count": blocked,
        "warning_count": warnings,
        "passed_count": sum(1 for gate in gates if gate.status == ProductionGateStatus.passed),
    }
    return ProductionReadinessReport(production_run_id, trade_date, as_of_date, status, gates, summary)


def _gate_data_freeze(data_freeze_dir: str | Path | None, required: bool) -> ProductionGateResult:
    if not data_freeze_dir:
        status = ProductionGateStatus.blocked if required else ProductionGateStatus.warning
        return ProductionGateResult("data_freeze_valid", status, "error" if required else "warning", "data freeze is missing", recommended_action="create or pass research data freeze")
    path = Path(data_freeze_dir)
    if not path.exists():
        return ProductionGateResult("data_freeze_valid", ProductionGateStatus.blocked, "error", f"data freeze directory does not exist: {path}", artifact_refs={"data_freeze_dir": str(path)})
    return ProductionGateResult("data_freeze_valid", ProductionGateStatus.passed, "info", "data freeze exists", artifact_refs={"data_freeze_dir": str(path)})


def _gate_calendar(data_dir: Path | None, trade_date: str) -> ProductionGateResult:
    if data_dir is None:
        return ProductionGateResult("market_calendar", ProductionGateStatus.warning, "warning", "data_dir missing")
    calendar = ProductionCalendar(data_dir)
    context = calendar.context(trade_date)
    if not context["is_trade_date"]:
        status = ProductionGateStatus.warning if calendar.trade_dates else ProductionGateStatus.blocked
        return ProductionGateResult("market_calendar", status, "warning", "trade_date is not in local calendar", value=context)
    return ProductionGateResult("market_calendar", ProductionGateStatus.passed, "info", "trade_date is open", value=context)


def _gate_model_registry(model_registry_dir: str | Path | None, require_active_model: bool, require_active_optimizer_policy: bool) -> list[ProductionGateResult]:
    if not model_registry_dir:
        return [
            ProductionGateResult("active_model", ProductionGateStatus.blocked if require_active_model else ProductionGateStatus.skipped, "error" if require_active_model else "info", "model registry dir missing"),
            ProductionGateResult("active_optimizer_policy", ProductionGateStatus.blocked if require_active_optimizer_policy else ProductionGateStatus.skipped, "error" if require_active_optimizer_policy else "info", "model registry dir missing"),
        ]
    registry = LocalModelRegistry(model_registry_dir)
    active_model = registry.latest_active()
    active_policy = registry.latest_active_optimizer_policy()
    return [
        ProductionGateResult("active_model", ProductionGateStatus.passed if active_model else ProductionGateStatus.blocked if require_active_model else ProductionGateStatus.warning, "info" if active_model else "error" if require_active_model else "warning", "active model found" if active_model else "active composite model missing", value=active_model.model_version_id if active_model else None),
        ProductionGateResult("active_optimizer_policy", ProductionGateStatus.passed if active_policy else ProductionGateStatus.blocked if require_active_optimizer_policy else ProductionGateStatus.warning, "info" if active_policy else "error" if require_active_optimizer_policy else "warning", "active optimizer policy found" if active_policy else "active optimizer policy missing", value=active_policy.model_version_id if active_policy else None),
    ]


def _gate_portfolio_certification(
    policy_path: str | Path | None,
    decision_path: str | Path | None,
    required: bool,
    *,
    model_registry_dir: str | Path | None = None,
) -> ProductionGateResult:
    payload = _read_json(decision_path) or _read_json(policy_path)
    refs = _refs(policy_path, decision_path)
    if not payload and model_registry_dir:
        active_policy = LocalModelRegistry(model_registry_dir).latest_active_optimizer_policy()
        if active_policy:
            metadata = dict(active_policy.metadata or {})
            nested_policy = metadata.get("portfolio_policy") if isinstance(metadata.get("portfolio_policy"), dict) else {}
            status = str(
                active_policy.gate_status
                or metadata.get("certification_status")
                or nested_policy.get("certification_status")
                or ""
            )
            if status in {"certified", "conditional"}:
                refs.update({k: v for k, v in active_policy.source_artifacts.items() if isinstance(v, str)})
                return ProductionGateResult(
                    "portfolio_certification",
                    ProductionGateStatus.passed,
                    "info",
                    "active optimizer policy is certified",
                    value=status,
                    artifact_refs=refs,
                )
            payload = {"status": status}
    status = str(payload.get("status") or payload.get("certification_status") or "")
    if status in {"certified", "conditional"}:
        return ProductionGateResult("portfolio_certification", ProductionGateStatus.passed, "info", "portfolio certification passed", value=status, artifact_refs=refs)
    if required:
        return ProductionGateResult("portfolio_certification", ProductionGateStatus.blocked, "error", "portfolio certification missing or not passed", value=status, artifact_refs=refs)
    return ProductionGateResult("portfolio_certification", ProductionGateStatus.warning, "warning", "portfolio certification not provided", value=status, artifact_refs=refs)


def _gate_account(account_dir: str | Path | None) -> ProductionGateResult:
    if not account_dir:
        return ProductionGateResult("account_state", ProductionGateStatus.warning, "warning", "paper account dir missing")
    state_path = Path(account_dir) / "account_state.json"
    return ProductionGateResult("account_state", ProductionGateStatus.passed if state_path.exists() else ProductionGateStatus.warning, "info" if state_path.exists() else "warning", "account state readable" if state_path.exists() else "account state missing", artifact_refs={"account_state_path": str(state_path)})


def _gate_risk_state(risk_control_state_dir: str | Path | None) -> ProductionGateResult:
    if not risk_control_state_dir:
        return ProductionGateResult("risk_kill_switch", ProductionGateStatus.warning, "warning", "risk control state dir missing")
    state = LocalRiskControlState(risk_control_state_dir).load_kill_switch()
    if state.active:
        return ProductionGateResult("risk_kill_switch", ProductionGateStatus.blocked, "error", "risk kill switch is active", value=state.reason)
    return ProductionGateResult("risk_kill_switch", ProductionGateStatus.passed, "info", "risk kill switch inactive")


def _gate_go_live(
    decision_path: str | Path | None,
    required: bool,
    compliance_pack_path: str | Path | None,
    broker_uat_report_path: str | Path | None,
) -> ProductionGateResult:
    payload = _read_json(decision_path)
    status = str(payload.get("status") or "")
    accepted = {"ready_for_file_outbox_dry_run", "ready_for_manual_pilot_review"}
    refs = _refs(decision_path, compliance_pack_path, broker_uat_report_path)
    if not payload:
        gate_status = ProductionGateStatus.blocked if required else ProductionGateStatus.skipped
        return ProductionGateResult("go_live_gate", gate_status, "error" if required else "info", "go-live gate decision missing", artifact_refs=refs)
    if status in accepted:
        return ProductionGateResult("go_live_gate", ProductionGateStatus.passed, "info", "go-live gate accepts local file dry-run or manual review", value=status, artifact_refs=refs)
    gate_status = ProductionGateStatus.blocked if required else ProductionGateStatus.warning
    return ProductionGateResult("go_live_gate", gate_status, "error" if required else "warning", "go-live gate is not in an accepted local status", value=status, artifact_refs=refs)


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


def _refs(*paths: str | Path | None) -> dict[str, str]:
    return {f"path_{idx}": str(path) for idx, path in enumerate(paths) if path}
