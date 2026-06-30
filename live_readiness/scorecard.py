"""Build a live readiness scorecard from production artifacts."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import LiveReadinessCheck, LiveReadinessPolicy, LiveReadinessScorecard, LiveReadinessStatus


def build_live_readiness_scorecard(
    policy: LiveReadinessPolicy,
    production_replay_report_path: str | Path | None = None,
    shadow_lab_report_path: str | Path | None = None,
    incident_report_path: str | Path | None = None,
    monitoring_report_path: str | Path | None = None,
    model_registry_report_path: str | Path | None = None,
    factor_certification_decision_path: str | Path | None = None,
    portfolio_certification_decision_path: str | Path | None = None,
    freeze_validation_report_path: str | Path | None = None,
    risk_control_report_path: str | Path | None = None,
    settlement_report_path: str | Path | None = None,
    eod_reconciliation_report_path: str | Path | None = None,
    broker_mapping_certification_decision_path: str | Path | None = None,
    broker_file_gateway_report_path: str | Path | None = None,
    operator_handoff_report_path: str | Path | None = None,
) -> LiveReadinessScorecard:
    replay = _read_json(production_replay_report_path)
    shadow = _read_json(shadow_lab_report_path)
    incidents = _read_json(incident_report_path)
    monitoring = _read_json(monitoring_report_path)
    factor_cert = _read_json(factor_certification_decision_path)
    portfolio_cert = _read_json(portfolio_certification_decision_path)
    freeze = _read_json(freeze_validation_report_path)
    risk = _read_json(risk_control_report_path)
    settlement = _read_json(settlement_report_path)
    eod = _read_json(eod_reconciliation_report_path)
    mapping_cert = _read_json(broker_mapping_certification_decision_path)
    gateway = _read_json(broker_file_gateway_report_path)
    handoff = _read_json(operator_handoff_report_path)
    replay_summary = replay.get("summary") or {}

    checks = [
        _check(
            "replay_day_count",
            int((replay.get("summary") or {}).get("replay_day_count", 0) or 0) >= policy.min_replay_days,
            int((replay.get("summary") or {}).get("replay_day_count", 0) or 0),
            policy.min_replay_days,
            "run additional production replay days",
        ),
        _check(
            "replay_failed_days",
            int((replay.get("summary") or {}).get("replay_failed_day_count", 0) or 0) <= policy.max_failed_replay_days,
            int((replay.get("summary") or {}).get("replay_failed_day_count", 0) or 0),
            policy.max_failed_replay_days,
            "resolve failed replay days",
        ),
        _check(
            "replay_blocked_days",
            int((replay.get("summary") or {}).get("replay_blocked_day_count", 0) or 0) <= policy.max_blocked_replay_days,
            int((replay.get("summary") or {}).get("replay_blocked_day_count", 0) or 0),
            policy.max_blocked_replay_days,
            "clear blocked readiness gates",
        ),
        _check(
            "shadow_day_count",
            int((shadow.get("performance_summary") or {}).get("shadow_day_count", 0) or 0) >= policy.min_shadow_days,
            int((shadow.get("performance_summary") or {}).get("shadow_day_count", 0) or 0),
            policy.min_shadow_days,
            "run more shadow days",
        ),
        _check(
            "shadow_drift",
            max(
                abs(float((shadow.get("drift_summary") or {}).get("shadow_target_weight_drift", 0.0) or 0.0)),
                abs(float((shadow.get("drift_summary") or {}).get("shadow_position_weight_drift", 0.0) or 0.0)),
            )
            <= policy.max_shadow_drift,
            max(
                abs(float((shadow.get("drift_summary") or {}).get("shadow_target_weight_drift", 0.0) or 0.0)),
                abs(float((shadow.get("drift_summary") or {}).get("shadow_position_weight_drift", 0.0) or 0.0)),
            ),
            policy.max_shadow_drift,
            "calibrate shadow execution assumptions",
        ),
        _check(
            "shadow_fill_rate",
            float((shadow.get("performance_summary") or {}).get("shadow_average_fill_rate", 0.0) or 0.0) >= policy.min_average_fill_rate,
            float((shadow.get("performance_summary") or {}).get("shadow_average_fill_rate", 0.0) or 0.0),
            policy.min_average_fill_rate,
            "review capacity and execution plan settings",
        ),
        _check(
            "shadow_rejection_rate",
            float((shadow.get("performance_summary") or {}).get("shadow_order_rejection_rate", 0.0) or 0.0) <= policy.max_order_rejection_rate,
            float((shadow.get("performance_summary") or {}).get("shadow_order_rejection_rate", 0.0) or 0.0),
            policy.max_order_rejection_rate,
            "reduce rejected orders before live gate",
        ),
        _optional_check("factor_certification", not policy.require_certified_factor or _passed(factor_cert), policy.require_certified_factor, "complete factor production certification"),
        _optional_check("portfolio_certification", not policy.require_certified_portfolio or _passed(portfolio_cert), policy.require_certified_portfolio, "complete portfolio policy certification"),
        _optional_check("data_freeze", not policy.require_data_freeze or _passed(freeze), policy.require_data_freeze, "freeze and validate research data"),
        _optional_check("risk_controls", not policy.require_risk_controls or _passed(risk), policy.require_risk_controls, "enable and pass pre-trade risk controls"),
        _optional_check("settlement", _not_failed(settlement), False, "review settlement report"),
        _optional_check("eod_reconciliation", _not_failed(eod), False, "review day-end reconciliation"),
        _optional_check("incidents", int((incidents.get("summary") or {}).get("error_count", 0) or 0) <= policy.max_incident_error_count, False, "close high severity incidents"),
        _optional_check("monitoring", _not_failed(monitoring), False, "review monitoring alerts"),
        _optional_check("model_registry", _not_failed(_read_json(model_registry_report_path)), False, "review model registry report"),
        _optional_check(
            "broker_mapping_certification",
            not policy.require_broker_mapping_certification or str(mapping_cert.get("status") or "") == "certified_for_dry_run",
            policy.require_broker_mapping_certification,
            "complete broker mapping dry-run certification",
        ),
        _check(
            "file_outbox_replay_day_count",
            int(replay_summary.get("file_outbox_day_count", 0) or 0) >= policy.min_file_outbox_replay_days,
            int(replay_summary.get("file_outbox_day_count", 0) or 0),
            policy.min_file_outbox_replay_days,
            "run more file outbox dry-run replay days",
        )
        if policy.min_file_outbox_replay_days > 0
        else _optional_check("file_outbox_replay_day_count", True, False, "run file outbox replay"),
        _optional_check(
            "broker_file_roundtrip",
            not policy.require_broker_file_gateway_roundtrip
            or (bool(gateway) and int((gateway.get("summary") or {}).get("roundtrip_error_count", gateway.get("roundtrip_error_count", 0)) or 0) <= policy.max_file_roundtrip_errors),
            policy.require_broker_file_gateway_roundtrip,
            "fix broker file roundtrip issues",
        ),
        _optional_check(
            "operator_handoff",
            not policy.require_operator_handoff
            or (bool(handoff) and len(handoff.get("missing_required_items") or []) <= policy.max_missing_handoff_items),
            policy.require_operator_handoff,
            "complete required operator handoff checklist",
        ),
        _optional_check(
            "file_outbox_no_real_submit",
            not policy.require_no_real_submit
            or (
                not bool((gateway.get("summary") or {}).get("file_outbox_real_submit_detected", gateway.get("file_outbox_real_submit_detected", False)))
                and not bool(replay_summary.get("file_outbox_real_submit_detected", False))
            ),
            policy.require_no_real_submit,
            "remove any real submit path from file outbox dry-run",
        ),
    ]
    failed_required = [check for check in checks if check.required and check.status != "passed"]
    warning_count = sum(1 for check in checks if not check.required and check.status != "passed")
    score = sum(check.score for check in checks) / len(checks) if checks else 0.0
    status = _status_from_checks(policy, failed_required)
    summary = {
        "readiness_failed_check_count": len(failed_required),
        "readiness_warning_check_count": warning_count,
        "readiness_required_remediation_count": len(failed_required),
        "score": score,
    }
    return LiveReadinessScorecard(status, score, _utc_now(), policy.to_dict(), checks, summary)


def readiness_target_status(policy: LiveReadinessPolicy) -> str:
    if policy.profile == "paper_standard":
        return LiveReadinessStatus.ready_for_paper_simulated
    if policy.profile == "file_outbox_dry_run_strict":
        return LiveReadinessStatus.ready_for_file_outbox_dry_run
    return LiveReadinessStatus.ready_for_shadow


def _status_from_checks(policy: LiveReadinessPolicy, failed_required: list[LiveReadinessCheck]) -> str:
    if not failed_required:
        return readiness_target_status(policy)
    insufficient_ids = {"replay_day_count", "shadow_day_count", "file_outbox_replay_day_count"}
    if any(check.check_id in insufficient_ids for check in failed_required):
        return LiveReadinessStatus.insufficient_data
    return LiveReadinessStatus.not_ready


def _check(check_id: str, passed: bool, value: Any, threshold: Any, remediation: str) -> LiveReadinessCheck:
    return LiveReadinessCheck(check_id, "passed" if passed else "failed", 1.0 if passed else 0.0, "passed" if passed else "threshold not met", True, value, threshold, remediation)


def _optional_check(check_id: str, passed: bool, required: bool, remediation: str) -> LiveReadinessCheck:
    return LiveReadinessCheck(check_id, "passed" if passed else "failed", 1.0 if passed else 0.0, "passed" if passed else "check did not pass", required, None, None, remediation)


def _passed(payload: dict[str, Any]) -> bool:
    if not payload:
        return False
    if payload.get("passed") is True:
        return True
    status = str(payload.get("status") or payload.get("new_status") or "").lower()
    return status in {"passed", "approved", "production_candidate", "success", "ok", "ready", "certified_for_dry_run"}


def _not_failed(payload: dict[str, Any]) -> bool:
    if not payload:
        return True
    return str(payload.get("status") or "").lower() not in {"failed", "error", "blocked", "not_ready"}


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
