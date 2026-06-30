"""Build Go/No-Go scorecards from local artifacts."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import GoLiveGateCheck, GoLiveGatePolicy, GoLiveGateScorecard, GoLiveGateStatus


def build_go_live_scorecard(policy: GoLiveGatePolicy, **paths: str | Path | None) -> GoLiveGateScorecard:
    checks = [
        _check_exists("compliance_pack_check", paths.get("program_trading_compliance_pack_path"), policy.require_compliance_pack),
        _check_secret(paths.get("secret_scan_report_path"), policy),
        _check_uat(paths.get("broker_uat_report_path"), paths.get("broker_adapter_contract_report_path"), policy),
        _check_status("mapping_certification_check", paths.get("broker_mapping_certification_decision_path"), {"certified_for_dry_run"}, policy.require_mapping_certification),
        _check_file_gateway(paths.get("broker_file_gateway_report_path"), policy),
        _check_exists("operator_handoff_check", paths.get("operator_handoff_report_path"), policy.require_operator_handoff),
        _check_replay(paths.get("production_replay_report_path"), policy),
        _check_exists("shadow_replay_check", paths.get("shadow_lab_report_path"), policy.require_shadow_replay),
        _check_status("factor_certification_check", paths.get("factor_certification_decision_path"), {"certified", "conditional"}, policy.require_factor_certification),
        _check_status("portfolio_certification_check", paths.get("portfolio_certification_decision_path"), {"certified", "conditional"}, policy.require_portfolio_certification),
        _check_exists("risk_control_check", paths.get("risk_control_report_path"), policy.require_risk_controls),
        _check_kill_switch(paths.get("risk_control_report_path"), policy),
        _check_exists("settlement_check", paths.get("settlement_report_path"), False),
        _check_exists("eod_reconciliation_check", paths.get("eod_reconciliation_report_path"), policy.require_eod_reconciliation),
        _check_incident(paths.get("incident_report_path"), policy),
        _check_exists("monitoring_check", paths.get("monitoring_report_path"), False),
        _check_exists("release_gate_check", paths.get("release_gate_report_path"), policy.require_release_gate),
        GoLiveGateCheck("no_real_submit_path_check", "passed", "info", False, False, "real broker submit path is not supported", required=True),
        GoLiveGateCheck("human_review_required_check", "passed", "info", True, True, "human review remains required", required=True),
    ]
    required_failed = [check for check in checks if check.required and check.status != "passed"]
    warnings = [check for check in checks if not check.required and check.status != "passed"]
    score = (len(checks) - len(required_failed) - 0.25 * len(warnings)) / max(len(checks), 1)
    if required_failed:
        status = GoLiveGateStatus.insufficient_data if any(check.status == "missing" for check in required_failed) else GoLiveGateStatus.not_ready
    elif policy.profile_name == "manual_pilot_review_strict":
        status = GoLiveGateStatus.ready_for_manual_pilot_review
    elif policy.require_file_outbox_dry_run:
        status = GoLiveGateStatus.ready_for_file_outbox_dry_run
    else:
        status = GoLiveGateStatus.ready_for_broker_uat
    return GoLiveGateScorecard(
        status=status,
        score=float(max(min(score, 1.0), 0.0)),
        created_at=_utc_now(),
        policy=policy.to_dict(),
        checks=checks,
        summary={
            "check_count": len(checks),
            "required_failed_count": len(required_failed),
            "warning_count": len(warnings),
            "blocker_count": sum(1 for check in checks if check.severity == "blocker" and check.status != "passed"),
        },
    )


def _check_exists(check_id: str, path: str | Path | None, required: bool) -> GoLiveGateCheck:
    exists = bool(path) and Path(path).exists()
    return GoLiveGateCheck(
        check_id,
        "passed" if exists else "missing",
        "info" if exists else "error" if required else "warning",
        str(path) if path else "",
        "exists",
        "artifact exists" if exists else "artifact missing",
        {"path": str(path)} if path else {},
        required,
    )


def _check_status(check_id: str, path: str | Path | None, accepted: set[str], required: bool) -> GoLiveGateCheck:
    payload = _read_json(path)
    status = str(payload.get("status") or payload.get("decision") or "")
    ok = bool(payload) and status in accepted
    return GoLiveGateCheck(
        check_id,
        "passed" if ok else "failed" if payload else "missing",
        "info" if ok else "error" if required else "warning",
        status,
        sorted(accepted),
        "accepted status" if ok else "status missing or not accepted",
        {"path": str(path)} if path else {},
        required,
    )


def _check_secret(path: str | Path | None, policy: GoLiveGatePolicy) -> GoLiveGateCheck:
    payload = _read_json(path)
    blockers = int(payload.get("blocker_count", 0) or 0) if payload else 0
    ok = payload and blockers <= policy.max_secret_blockers
    return GoLiveGateCheck(
        "secret_scan_check",
        "passed" if ok else "failed" if payload else "missing",
        "info" if ok else "blocker" if blockers else "error" if policy.require_secret_scan_clean else "warning",
        blockers,
        policy.max_secret_blockers,
        "secret scan clean" if ok else "secret scan has blockers or is missing",
        {"path": str(path)} if path else {},
        policy.require_secret_scan_clean,
    )


def _check_uat(uat_path: str | Path | None, contract_path: str | Path | None, policy: GoLiveGatePolicy) -> GoLiveGateCheck:
    payload = _read_json(uat_path)
    contract = _read_json(contract_path)
    failed = int((payload.get("summary") or {}).get("failed_count", 0) or payload.get("failed_count", 0) or 0) if payload else 0
    if not failed and contract:
        failed = int(contract.get("failed_count", 0) or 0)
    ok = payload and failed <= policy.max_uat_failed_scenarios
    return GoLiveGateCheck(
        "broker_uat_contract_check",
        "passed" if ok else "failed" if payload else "missing",
        "info" if ok else "error",
        failed,
        policy.max_uat_failed_scenarios,
        "BrokerAdapter UAT passed" if ok else "BrokerAdapter UAT missing or has failed scenarios",
        {"broker_uat_report_path": str(uat_path or ""), "broker_adapter_contract_report_path": str(contract_path or "")},
        policy.require_broker_uat,
    )


def _check_file_gateway(path: str | Path | None, policy: GoLiveGatePolicy) -> GoLiveGateCheck:
    payload = _read_json(path)
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    errors = int(summary.get("roundtrip_error_count", payload.get("roundtrip_error_count", 0)) or 0) if payload else 0
    real_submit = bool(summary.get("file_outbox_real_submit_detected", payload.get("file_outbox_real_submit_detected", False))) if payload else False
    ok = payload and errors <= policy.max_file_roundtrip_errors and not real_submit
    return GoLiveGateCheck(
        "broker_file_roundtrip_check",
        "passed" if ok else "failed" if payload else "missing",
        "info" if ok else "blocker" if real_submit else "error" if policy.require_file_outbox_dry_run else "warning",
        {"roundtrip_errors": errors, "real_submit_detected": real_submit},
        {"max_errors": policy.max_file_roundtrip_errors, "real_submit_detected": False},
        "file outbox dry-run evidence accepted" if ok else "file outbox evidence missing or failed",
        {"path": str(path)} if path else {},
        policy.require_file_outbox_dry_run,
    )


def _check_replay(path: str | Path | None, policy: GoLiveGatePolicy) -> GoLiveGateCheck:
    payload = _read_json(path)
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else payload
    days = int(summary.get("file_outbox_day_count", summary.get("replay_day_count", 0)) or 0) if payload else 0
    required = policy.require_file_outbox_dry_run or policy.require_paper_replay
    ok = payload and days >= policy.min_file_outbox_dryrun_days
    return GoLiveGateCheck(
        "file_outbox_dryrun_check",
        "passed" if ok else "missing",
        "info" if ok else "error" if required else "warning",
        days,
        policy.min_file_outbox_dryrun_days,
        "production replay evidence accepted" if ok else "production replay evidence missing",
        {"path": str(path)} if path else {},
        required,
    )


def _check_kill_switch(path: str | Path | None, policy: GoLiveGatePolicy) -> GoLiveGateCheck:
    exists = bool(path) and Path(path).exists()
    return GoLiveGateCheck(
        "kill_switch_check",
        "passed" if exists or not policy.require_kill_switch_available else "missing",
        "info" if exists else "error" if policy.require_kill_switch_available else "warning",
        exists,
        True,
        "kill switch evidence available" if exists else "kill switch evidence missing",
        {"path": str(path)} if path else {},
        policy.require_kill_switch_available,
    )


def _check_incident(path: str | Path | None, policy: GoLiveGatePolicy) -> GoLiveGateCheck:
    payload = _read_json(path)
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    critical = int(summary.get("critical_open_count", summary.get("open_critical_count", 0)) or 0) if payload else 0
    ok = critical <= policy.max_unresolved_incidents
    return GoLiveGateCheck(
        "incident_check",
        "passed" if ok else "failed",
        "info" if ok else "blocker",
        critical,
        policy.max_unresolved_incidents,
        "no open critical incidents" if ok else "open critical incident blocker",
        {"path": str(path)} if path else {},
        policy.require_no_open_critical_incidents,
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


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
