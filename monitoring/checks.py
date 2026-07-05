"""Local production monitoring checks."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from factor_store import LocalFactorStore
from paper_account import LocalPaperAccount, compute_account_performance
from broker_adapter import LocalBrokerStore
from model_registry import LocalModelRegistry

from .models import MonitoringAlert


def check_real_data_readiness(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    status = str(payload.get("status") or "")
    alerts = []
    if status in {"blocked", "failed", "error"}:
        alerts.append(MonitoringAlert("error", "real_data_readiness", "real data readiness is blocked", {"status": status}))
    return {
        "exists": bool(payload),
        "real_data_pipeline_status": status,
        "real_data_profile_name": payload.get("profile_name", ""),
        "request_budget_used": int(payload.get("request_budget_used", payload.get("estimated_requests", 0)) or 0),
    }, alerts


def check_api_permission_matrix(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    issue_count = int(payload.get("permission_issue_count", 0) or 0)
    alerts = [MonitoringAlert("warning", "api_permission_matrix", "API permission matrix contains issues", {"count": issue_count})] if issue_count else []
    return {"exists": bool(payload), "api_permission_issue_count": issue_count}, alerts


def check_real_data_sla(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    status = str(payload.get("status") or "")
    alerts = []
    if status == "fail":
        alerts.append(MonitoringAlert("error", "real_data_sla", "real data SLA failed"))
    elif status == "warning":
        alerts.append(MonitoringAlert("warning", "real_data_sla", "real data SLA has warnings"))
    return {"exists": bool(payload), "real_data_sla_status": status, "real_data_sla_summary": payload.get("summary", {})}, alerts


def check_real_data_size(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    return {
        "exists": bool(payload),
        "total_data_size_gb": float(payload.get("total_size_gb", 0.0) or 0.0),
        "matrix_cache_size_gb": float(payload.get("matrix_cache_size_bytes", 0) or 0) / (1024**3),
    }, []


def check_matrix_refresh(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    status = str(payload.get("status") or "")
    alerts = [MonitoringAlert("warning", "matrix_refresh", "matrix refresh is not fresh", {"status": status})] if status in {"failed", "stale"} else []
    return {
        "exists": bool(payload),
        "matrix_refresh_status": status,
        "matrix_source_hash_drift_count": int(((payload.get("source_diff") or {}).get("drift_count", 0)) if isinstance(payload.get("source_diff"), dict) else 0),
    }, alerts


def check_matrix_freshness(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    status = str(payload.get("status") or "")
    alerts = [MonitoringAlert("warning", "matrix_freshness", "matrix freshness check is not fresh", {"status": status})] if status not in {"", "fresh"} else []
    return {"exists": bool(payload), "matrix_freshness_status": status, "n_stocks": payload.get("n_stocks", 0), "n_dates": payload.get("n_dates", 0)}, alerts


def check_real_data_token_redaction(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not path or not Path(path).exists():
        return {"exists": False, "token_redaction_passed": True}, []
    text = Path(path).read_text(encoding="utf-8")
    leaked = "TUSHARE_TOKEN=" in text
    alerts = [MonitoringAlert("error", "real_data_token_redaction", "token-like value leaked in real data artifact")] if leaked else []
    return {"exists": True, "token_redaction_passed": not leaked}, alerts


def check_data_freshness(data_dir: str | Path, as_of_date: str) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    records = _read_jsonl(Path(data_dir) / "trade_calendar" / "records.jsonl")
    open_dates = sorted(str(record.get("trade_date")) for record in records if record.get("is_open") is True)
    latest = open_dates[-1] if open_dates else ""
    ok = latest >= as_of_date if latest else False
    alerts = [] if ok else [MonitoringAlert("error", "data_freshness", f"latest open trade date {latest or 'missing'} is before {as_of_date}")]
    return {"latest_trade_date": latest, "as_of_date": as_of_date, "ok": ok}, alerts


def check_quality_report(data_dir: str | Path) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(data_dir) / "quality_report.json")
    errors = int(payload.get("total_errors") or 0) if payload else 0
    warnings = int(payload.get("total_warnings") or 0) if payload else 0
    alerts = []
    if errors > 0:
        alerts.append(MonitoringAlert("error", "quality_report", "quality_report contains errors", {"total_errors": errors}))
    elif warnings > 0:
        alerts.append(MonitoringAlert("warning", "quality_report", "quality_report contains warnings", {"total_warnings": warnings}))
    return {"exists": bool(payload), "total_errors": errors, "total_warnings": warnings, "ok": errors == 0}, alerts


def check_factor_drift(store: LocalFactorStore, factor_id: str | None, recent_window: int = 5) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if factor_id is None:
        record = store.load_latest_factor(status="production_candidate", factor_type="composite") or store.load_latest_factor(
            status="approved", factor_type="composite"
        )
        factor_id = record.factor_id if record is not None else None
    if factor_id is None:
        alert = MonitoringAlert("warning", "factor_drift", "no production or approved composite factor found")
        return {"factor_id": None, "ok": False}, [alert]
    values = [record.value for record in store.load_factor_values(factor_id) if record.value is not None]
    recent = values[-max(recent_window, 1) :]
    mean = sum(recent) / len(recent) if recent else 0.0
    variance = sum((value - mean) ** 2 for value in recent) / len(recent) if recent else 0.0
    std = math.sqrt(max(variance, 0.0))
    zscore = abs(mean) / std if std > 1e-12 else 0.0
    alerts = []
    if zscore > 5:
        alerts.append(MonitoringAlert("warning", "factor_drift", "recent factor values have elevated mean zscore", {"zscore": zscore}))
    return {"factor_id": factor_id, "records": len(values), "recent_mean": mean, "recent_std": std, "recent_zscore": zscore, "ok": True}, alerts


def check_risk_report(risk_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not risk_report_path:
        return {"exists": False, "violations": 0}, []
    payload = _read_json(Path(risk_report_path))
    violations = payload.get("violations", []) if payload else []
    alerts = []
    if violations:
        alerts.append(MonitoringAlert("warning", "risk_report", "risk report contains constraint violations", {"violations": violations}))
    return {"exists": bool(payload), "violations": len(violations), "metrics": payload.get("metrics", {}) if payload else {}}, alerts


def check_style_exposure_drift(risk_exposures_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not risk_exposures_path:
        return {"exists": False, "rows": 0}, []
    rows = _read_jsonl(Path(risk_exposures_path))
    max_style = max((abs(float(row.get("max_style_exposure_abs", 0.0) or 0.0)) for row in rows), default=0.0)
    max_active_style = max((abs(float(row.get("max_active_style_exposure_abs", 0.0) or 0.0)) for row in rows), default=0.0)
    alerts = []
    if max_active_style > 2.0:
        alerts.append(
            MonitoringAlert(
                "warning",
                "style_exposure_drift",
                "active style exposure is elevated",
                {"max_active_style_exposure_abs": max_active_style},
            )
        )
    return {
        "exists": bool(rows),
        "rows": len(rows),
        "max_style_exposure_abs": max_style,
        "max_active_style_exposure_abs": max_active_style,
    }, alerts


def check_active_risk_drift(risk_decomposition_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not risk_decomposition_path:
        return {"exists": False, "rows": 0}, []
    rows = _read_jsonl(Path(risk_decomposition_path))
    active_risks = [
        float((row.get("active") or {}).get("total_risk", 0.0) or 0.0)
        for row in rows
        if isinstance(row.get("active"), dict)
    ]
    max_active_risk = max(active_risks, default=0.0)
    alerts = []
    if max_active_risk > 1.0:
        alerts.append(
            MonitoringAlert(
                "warning",
                "active_risk_drift",
                "active factor risk is elevated",
                {"max_active_risk": max_active_risk},
            )
        )
    return {"exists": bool(rows), "rows": len(rows), "max_active_risk": max_active_risk}, alerts


def check_factor_risk_concentration(risk_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not risk_report_path:
        return {"exists": False}, []
    payload = _read_json(Path(risk_report_path))
    contribution = payload.get("factor_risk_contribution", {}) if payload else {}
    factor_values = contribution.get("factor_contributions", {}) if isinstance(contribution, dict) else {}
    max_contribution = max((abs(float(value or 0.0)) for value in factor_values.values()), default=0.0)
    factor_share = float(contribution.get("factor_risk_share", 0.0) or 0.0) if isinstance(contribution, dict) else 0.0
    specific_share = float(contribution.get("specific_risk_share", 0.0) or 0.0) if isinstance(contribution, dict) else 0.0
    alerts = []
    if max_contribution > 0.90:
        alerts.append(
            MonitoringAlert(
                "warning",
                "factor_risk_concentration",
                "risk contribution is concentrated in one factor",
                {"max_factor_contribution": max_contribution},
            )
        )
    return {
        "exists": bool(payload),
        "max_factor_contribution": max_contribution,
        "factor_risk_share": factor_share,
        "specific_risk_share": specific_share,
    }, alerts


def check_attribution_anomaly(return_attribution_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not return_attribution_path:
        return {"exists": False, "rows": 0}, []
    rows = _read_jsonl(Path(return_attribution_path))
    active_returns = [abs(float(row.get("total_active_return", 0.0) or 0.0)) for row in rows]
    max_active_return = max(active_returns, default=0.0)
    alerts = []
    if max_active_return > 0.20:
        alerts.append(
            MonitoringAlert(
                "warning",
                "attribution_anomaly",
                "active return attribution has a large single-period move",
                {"max_abs_active_return": max_active_return},
            )
        )
    return {"exists": bool(rows), "rows": len(rows), "max_abs_active_return": max_active_return}, alerts


def check_capacity_warnings(capacity_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not capacity_report_path:
        return {"exists": False, "capacity_warning_count": 0}, []
    payload = _read_json(Path(capacity_report_path))
    portfolio = payload.get("portfolio", {}) if payload else {}
    count = int(portfolio.get("capacity_warning_count", 0) or 0) if isinstance(portfolio, dict) else 0
    impact = float(portfolio.get("estimated_impact_cost", 0.0) or 0.0) if isinstance(portfolio, dict) else 0.0
    alerts = []
    if count > 0:
        alerts.append(MonitoringAlert("warning", "capacity_warnings", "capacity report contains warnings", {"count": count}))
    return {"exists": bool(payload), "capacity_warning_count": count, "impact_cost_estimate": impact}, alerts


def check_execution_quality(execution_quality_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not execution_quality_path:
        return {"exists": False, "execution_fill_rate": 0.0}, []
    payload = _read_json(Path(execution_quality_path))
    fill_rate = float(payload.get("execution_fill_rate", 0.0) or 0.0) if payload else 0.0
    rejected = int(payload.get("rejected_child_orders", 0) or 0) if payload else 0
    partial = int(payload.get("partial_child_orders", 0) or 0) if payload else 0
    alerts = []
    if payload and fill_rate < 0.5:
        alerts.append(MonitoringAlert("warning", "execution_quality", "execution fill rate is low", {"execution_fill_rate": fill_rate}))
    return {"exists": bool(payload), "execution_fill_rate": fill_rate, "rejected_child_orders": rejected, "partial_child_orders": partial}, alerts


def check_unfilled_orders(execution_quality_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not execution_quality_path:
        return {"exists": False, "unfilled_order_value": 0.0}, []
    payload = _read_json(Path(execution_quality_path))
    unfilled = float(payload.get("unfilled_order_value", 0.0) or 0.0) if payload else 0.0
    alerts = []
    if unfilled > 0:
        alerts.append(MonitoringAlert("warning", "unfilled_orders", "execution plan has unfilled order value", {"unfilled_order_value": unfilled}))
    return {"exists": bool(payload), "unfilled_order_value": unfilled}, alerts


def check_impact_cost_spike(capacity_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not capacity_report_path:
        return {"exists": False, "impact_cost_ratio": 0.0}, []
    payload = _read_json(Path(capacity_report_path))
    portfolio = payload.get("portfolio", {}) if payload else {}
    impact = float(portfolio.get("estimated_impact_cost", 0.0) or 0.0) if isinstance(portfolio, dict) else 0.0
    total = float(portfolio.get("total_order_value", 0.0) or 0.0) if isinstance(portfolio, dict) else 0.0
    ratio = impact / total if total > 1e-12 else 0.0
    alerts = []
    if ratio > 0.01:
        alerts.append(MonitoringAlert("warning", "impact_cost_spike", "estimated impact cost ratio is elevated", {"impact_cost_ratio": ratio}))
    return {"exists": bool(payload), "impact_cost_estimate": impact, "impact_cost_ratio": ratio}, alerts


def check_broker_reconciliation(reconciliation_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not reconciliation_path:
        return {"exists": False, "broker_status_mismatch_count": 0, "broker_orphan_fills": 0}, []
    payload = _read_json(Path(reconciliation_path))
    status_mismatch = int(payload.get("status_mismatch_count", 0) or 0) if payload else 0
    orphan_fills = int(payload.get("orphan_fills", 0) or 0) if payload else 0
    unfilled = float(payload.get("unfilled_value", 0.0) or 0.0) if payload else 0.0
    alerts = []
    if status_mismatch:
        alerts.append(MonitoringAlert("warning", "broker_reconciliation", "broker reconciliation has status mismatches", {"count": status_mismatch}))
    if orphan_fills:
        alerts.append(MonitoringAlert("error", "broker_reconciliation", "broker reconciliation has orphan fills", {"orphan_fills": orphan_fills}))
    return {
        "exists": bool(payload),
        "broker_status_mismatch_count": status_mismatch,
        "broker_orphan_fills": orphan_fills,
        "broker_unfilled_value": unfilled,
        "issues": payload.get("issues", []) if payload else [],
    }, alerts


def check_open_broker_orders(broker_store_dir: str | Path | None, broker_batch_id: str | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not broker_store_dir:
        return {"exists": False, "broker_open_orders": 0}, []
    store = LocalBrokerStore(broker_store_dir)
    orders = store.load_orders(batch_id=broker_batch_id)
    open_orders = [order for order in orders if order.status not in {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}]
    alerts = []
    if open_orders:
        alerts.append(MonitoringAlert("warning", "open_broker_orders", "broker batch has open orders", {"count": len(open_orders)}))
    return {"exists": bool(orders), "broker_open_orders": len(open_orders), "orders": len(orders)}, alerts


def check_broker_rejected_orders(broker_store_dir: str | Path | None, broker_batch_id: str | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not broker_store_dir:
        return {"exists": False, "broker_rejected_orders": 0}, []
    store = LocalBrokerStore(broker_store_dir)
    rejected = [order for order in store.load_orders(batch_id=broker_batch_id) if order.status == "REJECTED"]
    alerts = []
    if rejected:
        alerts.append(MonitoringAlert("warning", "broker_rejected_orders", "broker batch has rejected orders", {"count": len(rejected)}))
    return {"exists": bool(rejected), "broker_rejected_orders": len(rejected)}, alerts


def check_broker_idempotency(broker_store_dir: str | Path | None, broker_batch_id: str | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not broker_store_dir or not broker_batch_id:
        return {"exists": False, "idempotent_replay_count": 0}, []
    store = LocalBrokerStore(broker_store_dir)
    replay_count = store.replay_count(broker_batch_id)
    return {"exists": True, "idempotent_replay_count": replay_count}, []


def check_broker_file_outbox(outbox_manifest_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not outbox_manifest_path:
        return {"exists": False, "broker_file_exported_orders": 0}, []
    payload = _read_json(Path(outbox_manifest_path))
    orders = int(payload.get("orders", 0) or 0) if payload else 0
    alerts = []
    if payload and orders == 0:
        alerts.append(MonitoringAlert("warning", "broker_file_outbox", "broker file outbox contains no orders"))
    return {
        "exists": bool(payload),
        "broker_file_exported_orders": orders,
        "schema_name": payload.get("schema_name", "") if payload else "",
    }, alerts


def check_broker_file_gateway_report(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    status = str(payload.get("status", summary.get("status", "")) or "")
    errors = int(summary.get("roundtrip_error_count", payload.get("roundtrip_error_count", 0)) or 0) if payload else 0
    missing_ack = int(summary.get("roundtrip_missing_ack_count", payload.get("roundtrip_missing_ack_count", 0)) or 0) if payload else 0
    real_submit = bool(summary.get("file_outbox_real_submit_detected", payload.get("file_outbox_real_submit_detected", False))) if payload else False
    alerts = []
    if errors or missing_ack:
        alerts.append(MonitoringAlert("warning", "broker_file_gateway", "broker file roundtrip has issues", {"errors": errors, "missing_ack": missing_ack}))
    if real_submit:
        alerts.append(MonitoringAlert("error", "broker_file_gateway", "file outbox dry-run detected real submit path"))
    return {
        "exists": bool(payload),
        "broker_file_gateway_status": status,
        "broker_file_roundtrip_error_count": errors,
        "broker_file_missing_ack_count": missing_ack,
        "file_outbox_real_submit_detected": real_submit,
        "no_real_submit": bool(summary.get("no_real_submit", payload.get("no_real_submit", False))) if payload else False,
    }, alerts


def check_broker_connectivity(
    report_path: str | Path | None,
    network_guard_path: str | Path | None = None,
    credential_manifest_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    report = _read_json(Path(report_path)) if report_path else {}
    guard_report = _read_json(Path(network_guard_path)) if network_guard_path else {}
    credentials = _read_json(Path(credential_manifest_path)) if credential_manifest_path else {}
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    guard = guard_report.get("network_guard", guard_report) if isinstance(guard_report, dict) else {}
    credential_summary = credentials.get("summary", {}) if isinstance(credentials.get("summary"), dict) else {}
    status = str(report.get("status", "") if report else "")
    readonly = bool(summary.get("readonly_only", False))
    real_submit = bool(summary.get("real_submit_supported", report.get("real_submit_supported", False))) if report else False
    secret_blockers = int(credential_summary.get("secret_blocker_count", 0) or 0) if credentials else 0
    alerts: list[MonitoringAlert] = []
    if report and status not in {"passed", "warning"}:
        alerts.append(MonitoringAlert("warning", "broker_connectivity", "broker connectivity probe did not pass", {"status": status}))
    if report and (not readonly or real_submit):
        alerts.append(MonitoringAlert("error", "broker_connectivity", "broker connectivity evidence is not read-only", {"readonly_only": readonly, "real_submit_supported": real_submit}))
    if secret_blockers:
        alerts.append(MonitoringAlert("error", "broker_connectivity", "credential reference manifest has secret blockers", {"secret_blocker_count": secret_blockers}))
    return {
        "exists": bool(report),
        "broker_connectivity_status": status,
        "broker_connectivity_mode": str(summary.get("connectivity_mode", "")),
        "broker_network_guard_status": str(summary.get("network_guard_status") or guard.get("status", "")),
        "broker_connectivity_readonly_only": readonly,
        "broker_connectivity_real_submit_supported": real_submit,
        "broker_connectivity_secret_blocker_count": secret_blockers,
        "credential_ref_count": int(credential_summary.get("credential_ref_count", 0) or 0) if credentials else 0,
    }, alerts


def check_broker_readonly_mirror(
    report_path: str | Path | None,
    reconciliation_report_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    report = _read_json(Path(report_path)) if report_path else {}
    reconciliation = _read_json(Path(reconciliation_report_path)) if reconciliation_report_path else {}
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    status = str(report.get("status", "") if report else "")
    breaks = int(reconciliation.get("break_count", summary.get("readonly_mirror_break_count", 0)) or 0) if (report or reconciliation) else 0
    real_submit = bool(summary.get("real_submit_supported", report.get("real_submit_supported", False))) if report else False
    alerts: list[MonitoringAlert] = []
    if breaks:
        alerts.append(MonitoringAlert("warning", "broker_readonly_mirror", "read-only broker mirror has reconciliation breaks", {"break_count": breaks}))
    if real_submit:
        alerts.append(MonitoringAlert("error", "broker_readonly_mirror", "read-only broker mirror unexpectedly reports real submit support"))
    return {
        "exists": bool(report),
        "broker_readonly_mirror_status": status,
        "broker_readonly_mirror_break_count": breaks,
        "broker_readonly_position_count": int(summary.get("readonly_position_count", 0) or 0) if summary else 0,
        "broker_readonly_order_count": int(summary.get("readonly_order_count", 0) or 0) if summary else 0,
        "broker_readonly_fill_count": int(summary.get("readonly_fill_count", 0) or 0) if summary else 0,
        "broker_readonly_real_submit_supported": real_submit,
    }, alerts


def check_operator_handoff_report(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    missing = payload.get("missing_required_items", []) if isinstance(payload.get("missing_required_items"), list) else []
    checked = int(payload.get("checked_required_items", 0) or 0) if payload else 0
    required = int(payload.get("required_items", 0) or 0) if payload else 0
    alerts = []
    if missing:
        alerts.append(MonitoringAlert("warning", "operator_handoff", "operator handoff checklist has missing required items", {"missing": missing}))
    return {
        "exists": bool(payload),
        "operator_handoff_status": str(payload.get("status", "")) if payload else "",
        "operator_handoff_missing_required_count": len(missing),
        "operator_handoff_checked_required_items": checked,
        "operator_handoff_required_items": required,
        "no_real_submit_confirmed": bool(payload.get("no_real_submit_confirmed", False)) if payload else False,
    }, alerts


def check_broker_mapping_certification(decision_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(decision_path)) if decision_path else {}
    status = str(payload.get("status", "") if payload else "")
    reasons = payload.get("reasons", []) if isinstance(payload.get("reasons"), list) else []
    alerts = []
    if payload and status != "certified_for_dry_run":
        alerts.append(MonitoringAlert("warning", "broker_mapping_certification", "broker mapping is not certified_for_dry_run", {"status": status, "reasons": reasons}))
    return {
        "exists": bool(payload),
        "broker_mapping_certification_status": status,
        "broker_mapping_certification_reason_count": len(reasons),
        "profile_id": payload.get("profile_id", "") if payload else "",
        "schema_name": payload.get("schema_name", "") if payload else "",
    }, alerts


def check_program_trading_compliance_pack(pack_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(pack_path)) if pack_path else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    status = str(payload.get("status", "") if payload else "")
    gap_count = int(summary.get("gap_count", 0) or 0)
    real_submit = bool(summary.get("real_broker_submit_supported", payload.get("real_broker_submit_supported", False))) if payload else False
    alerts = []
    if payload and status in {"failed"}:
        alerts.append(MonitoringAlert("error", "program_trading_compliance", "compliance pack status failed", {"status": status}))
    elif payload and gap_count:
        alerts.append(MonitoringAlert("warning", "program_trading_compliance", "compliance pack has gaps", {"gap_count": gap_count}))
    if real_submit:
        alerts.append(MonitoringAlert("error", "program_trading_compliance", "real submit path detected in compliance pack"))
    return {
        "exists": bool(payload),
        "compliance_pack_status": status,
        "compliance_gap_count": gap_count,
        "real_submit_path_detected": real_submit,
    }, alerts


def check_secret_scan(secret_scan_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(secret_scan_report_path)) if secret_scan_report_path else {}
    blockers = int(payload.get("blocker_count", 0) or 0) if payload else 0
    warnings = int(payload.get("warning_count", 0) or 0) if payload else 0
    alerts = []
    if blockers:
        alerts.append(MonitoringAlert("error", "secret_scan", "secret scan has blockers", {"blocker_count": blockers}))
    elif warnings:
        alerts.append(MonitoringAlert("warning", "secret_scan", "secret scan has warnings", {"warning_count": warnings}))
    return {"exists": bool(payload), "secret_scan_blocker_count": blockers, "secret_scan_warning_count": warnings}, alerts


def check_broker_uat_contract(uat_report_path: str | Path | None, contract_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    report = _read_json(Path(uat_report_path)) if uat_report_path else {}
    contract = _read_json(Path(contract_report_path)) if contract_report_path else {}
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    failed = int(summary.get("failed_count", contract.get("failed_count", 0)) or 0) if (report or contract) else 0
    status = str(report.get("status") or contract.get("status") or "")
    alerts = []
    if failed:
        alerts.append(MonitoringAlert("error", "broker_uat_contract", "BrokerAdapter UAT has failed scenarios", {"failed_count": failed}))
    return {
        "exists": bool(report or contract),
        "broker_uat_status": status,
        "broker_uat_failed_scenario_count": failed,
        "broker_adapter_contract_status": str(contract.get("status", "")) if contract else "",
    }, alerts


def check_go_live_gate(decision_path: str | Path | None, scorecard_path: str | Path | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    decision = _read_json(Path(decision_path)) if decision_path else {}
    scorecard = _read_json(Path(scorecard_path)) if scorecard_path else {}
    status = str(decision.get("status", "") if decision else "")
    remediation = decision.get("required_remediation", []) if isinstance(decision.get("required_remediation"), list) else []
    blocker_count = int((decision.get("metadata") or {}).get("blocker_count", 0) or 0) if isinstance(decision.get("metadata"), dict) else 0
    alerts = []
    if status == "not_ready" or blocker_count:
        alerts.append(MonitoringAlert("error", "go_live_gate", "go-live gate is not ready", {"status": status, "blocker_count": blocker_count}))
    elif status == "insufficient_data" or remediation:
        alerts.append(MonitoringAlert("warning", "go_live_gate", "go-live gate requires remediation", {"status": status, "remediation": len(remediation)}))
    return {
        "exists": bool(decision),
        "go_live_status": status,
        "go_live_blocker_count": blocker_count,
        "go_live_required_remediation_count": len(remediation),
        "ready_for_manual_pilot_review": status == "ready_for_manual_pilot_review",
        "scorecard_status": str(scorecard.get("status", "")) if scorecard else "",
    }, alerts


def check_no_real_submit_path(pack_path: str | Path | None, decision_path: str | Path | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    pack = _read_json(Path(pack_path)) if pack_path else {}
    decision = _read_json(Path(decision_path)) if decision_path else {}
    real_submit = bool(pack.get("real_broker_submit_supported", False)) if pack else False
    metadata = decision.get("metadata", {}) if isinstance(decision.get("metadata"), dict) else {}
    real_submit = real_submit or bool(metadata.get("real_broker_submit_enabled", False))
    alerts = [MonitoringAlert("error", "no_real_submit_path", "real submit path detected")] if real_submit else []
    return {"real_submit_path_detected": real_submit, "ok": not real_submit}, alerts


def check_go_live_required_remediation(decision_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    decision = _read_json(Path(decision_path)) if decision_path else {}
    remediation = decision.get("required_remediation", []) if isinstance(decision.get("required_remediation"), list) else []
    alerts = []
    if remediation:
        alerts.append(MonitoringAlert("warning", "go_live_required_remediation", "go-live gate has required remediation", {"count": len(remediation)}))
    return {"exists": bool(decision), "required_remediation_count": len(remediation)}, alerts


def check_manual_review_status(decision_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    decision = _read_json(Path(decision_path)) if decision_path else {}
    status = str(decision.get("status", "") if decision else "")
    return {"exists": bool(decision), "manual_review_status": status, "human_review_required": True}, []


def check_compute_cluster_resources(resource_snapshot_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(resource_snapshot_path)) if resource_snapshot_path else {}
    cuda_available = bool(payload.get("cuda_available", False)) if payload else False
    gpu_count = int(payload.get("cuda_device_count", 0) or 0) if payload else 0
    return {
        "exists": bool(payload),
        "cuda_available": cuda_available,
        "gpu_count_detected": gpu_count,
        "device_count": len(payload.get("devices", []) or []) if payload else 0,
    }, []


def check_gpu_availability(resource_snapshot_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    result, _alerts = check_compute_cluster_resources(resource_snapshot_path)
    alerts = []
    if result.get("exists") and not result.get("cuda_available"):
        alerts.append(MonitoringAlert("info", "gpu_availability", "CUDA GPU unavailable; compute jobs should use CPU fallback"))
    return result, alerts


def check_compute_job_failures(compute_run_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(compute_run_report_path)) if compute_run_report_path else {}
    failed = int(payload.get("failed_count", 0) or 0) if payload else 0
    timed_out = int(payload.get("timeout_count", 0) or 0) if payload else 0
    alerts = []
    if failed or timed_out:
        alerts.append(MonitoringAlert("error", "compute_job_failures", "compute run has failed or timed out jobs", {"failed": failed, "timed_out": timed_out}))
    return {
        "exists": bool(payload),
        "compute_job_count": int(payload.get("job_count", 0) or 0) if payload else 0,
        "compute_failed_job_count": failed,
        "compute_timeout_count": timed_out,
        "compute_success_count": int(payload.get("success_count", 0) or 0) if payload else 0,
        "total_gpu_allocated_seconds": float(payload.get("total_gpu_allocated_seconds", 0.0) or 0.0) if payload else 0.0,
    }, alerts


def check_compute_job_retries(compute_run_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(compute_run_report_path)) if compute_run_report_path else {}
    return {"exists": bool(payload), "compute_resumed_job_count": int(payload.get("resumed_count", 0) or 0) if payload else 0}, []


def check_stale_gpu_leases(gpu_leases_path: str | Path | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(gpu_leases_path)) if gpu_leases_path else []
    alerts = [MonitoringAlert("warning", "stale_gpu_leases", "GPU leases are still open", {"count": len(rows)})] if rows else []
    return {"exists": bool(rows), "stale_gpu_lease_count": len(rows)}, alerts


def check_cuda_oom(compute_run_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(compute_run_report_path)) if compute_run_report_path else {}
    count = int(payload.get("oom_error_count", 0) or 0) if payload else 0
    alerts = [MonitoringAlert("error", "cuda_oom", "compute run recorded CUDA OOM", {"count": count})] if count else []
    return {"exists": bool(payload), "cuda_oom_count": count}, alerts


def check_cpu_fallbacks(compute_run_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(compute_run_report_path)) if compute_run_report_path else {}
    return {"exists": bool(payload), "fallback_to_cpu_count": int(payload.get("fallback_to_cpu_count", 0) or 0) if payload else 0}, []


def check_experiment_shard_failures(experiment_run_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(experiment_run_report_path)) if experiment_run_report_path else {}
    failed = int(payload.get("failed_shard_count", 0) or 0) if payload else 0
    alerts = [MonitoringAlert("error", "experiment_shards", "experiment has failed shards", {"failed_shard_count": failed})] if failed else []
    return {
        "exists": bool(payload),
        "experiment_status": payload.get("status", "") if payload else "",
        "experiment_shard_count": int(payload.get("shard_count", 0) or 0) if payload else 0,
        "experiment_failed_shard_count": failed,
    }, alerts


def check_experiment_merge_status(experiment_merge_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(experiment_merge_report_path)) if experiment_merge_report_path else {}
    status = str(payload.get("status", "")) if payload else ""
    missing = int(payload.get("missing_shard_count", 0) or 0) if payload else 0
    alerts = []
    if status not in {"", "success"} or missing:
        alerts.append(MonitoringAlert("warning", "experiment_merge", "experiment merge is not clean", {"status": status, "missing_shard_count": missing}))
    return {"exists": bool(payload), "experiment_merge_status": status, "missing_shard_count": missing}, alerts


def check_gpu_throughput_regression(gpu_benchmark_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(gpu_benchmark_report_path)) if gpu_benchmark_report_path else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    return {
        "exists": bool(payload),
        "formula_eval_throughput": float(summary.get("formula_eval_formulas_per_second_gpu", 0.0) or summary.get("formula_eval_formulas_per_second_cpu", 0.0) or 0.0),
        "pretrain_samples_per_second": float(summary.get("pretrain_samples_per_second_cpu", 0.0) or 0.0),
        "scheduler_warning_count": 0,
    }, []


def check_pre_trade_risk_controls(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not report_path:
        return {"exists": False, "risk_control_status": ""}, []
    payload = _read_json(Path(report_path))
    status = str(payload.get("status") or "")
    rejected = int(payload.get("rejected_orders", 0) or 0) if payload else 0
    clipped = int(payload.get("clipped_orders", 0) or 0) if payload else 0
    warnings = int(payload.get("warning_count", 0) or 0) if payload else 0
    errors = int(payload.get("error_count", 0) or 0) if payload else 0
    blockers = int(payload.get("blocker_count", 0) or 0) if payload else 0
    alerts = []
    if blockers or errors:
        alerts.append(MonitoringAlert("error", "pre_trade_risk_controls", "pre-trade risk controls have errors", {"errors": errors, "blockers": blockers}))
    elif rejected or clipped or warnings:
        alerts.append(MonitoringAlert("warning", "pre_trade_risk_controls", "pre-trade risk controls require review", {"rejected": rejected, "clipped": clipped, "warnings": warnings}))
    return {
        "exists": bool(payload),
        "risk_control_status": status,
        "risk_control_rejected_orders": rejected,
        "risk_control_clipped_orders": clipped,
        "risk_control_warning_count": warnings,
        "risk_control_error_count": errors,
        "risk_control_blocker_count": blockers,
    }, alerts


def check_risk_limit_usage(usage_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(usage_path)) if usage_path else []
    breached = [row for row in rows if row.get("status") == "breached"]
    return {"exists": bool(rows), "risk_limit_usage_records": len(rows), "risk_limit_breached_records": len(breached)}, []


def check_kill_switch_state(kill_switch_state_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(kill_switch_state_path)) if kill_switch_state_path else {}
    active = bool(payload.get("active", False)) if payload else False
    alerts = [MonitoringAlert("error", "kill_switch_state", "risk kill switch is active", {"reason": payload.get("reason", "")})] if active else []
    return {"exists": bool(payload), "kill_switch_active": active, "kill_switch_reason": payload.get("reason", "") if payload else ""}, alerts


def check_risk_overrides(records_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(records_path)) if records_path else []
    active = [row for row in rows if row.get("status") in {"applied", "active"}]
    return {"exists": bool(rows), "risk_override_records": len(rows), "active_risk_overrides": len(active)}, []


def check_broker_statement_import(import_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not import_report_path:
        return {"exists": False, "broker_statement_imported": False, "broker_statement_parse_error_count": 0}, []
    payload = _read_json(Path(import_report_path))
    validation = payload.get("validation", {}) if payload else {}
    errors = int(validation.get("error_count", 0) or 0) if isinstance(validation, dict) else 0
    warnings = int(validation.get("warning_count", 0) or 0) if isinstance(validation, dict) else 0
    alerts = []
    if errors:
        alerts.append(MonitoringAlert("error", "broker_statement_import", "broker statement import has parse or validation errors", {"errors": errors}))
    elif warnings:
        alerts.append(MonitoringAlert("warning", "broker_statement_import", "broker statement import has warnings", {"warnings": warnings}))
    return {
        "exists": bool(payload),
        "broker_statement_imported": bool(payload),
        "status": payload.get("status", "") if payload else "",
        "broker_statement_parse_error_count": errors,
        "broker_statement_warning_count": warnings,
        "record_counts": ((payload.get("manifest") or {}).get("record_counts") if payload else {}) or {},
    }, alerts


def check_statement_staleness(manifest_path: str | Path | None, as_of_date: str) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not manifest_path:
        return {"exists": False, "statement_stale": False}, []
    payload = _read_json(Path(manifest_path))
    statement_date = str(payload.get("as_of_date") or payload.get("trade_date") or "") if payload else ""
    stale = bool(statement_date and as_of_date and statement_date < as_of_date)
    alerts = [MonitoringAlert("warning", "statement_staleness", "broker statement is older than monitoring as_of_date", {"statement_as_of_date": statement_date})] if stale else []
    return {"exists": bool(payload), "statement_stale": stale, "statement_as_of_date": statement_date, "synthetic": bool((payload.get("metadata") or {}).get("synthetic")) if payload else False}, alerts


def check_eod_reconciliation(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    if not payload:
        return {"exists": False, "eod_reconciliation_status": ""}, []
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    status = str(payload.get("status") or summary.get("status") or "")
    errors = int(summary.get("error_count", 0) or 0)
    blockers = int(summary.get("blocker_count", 0) or 0)
    alerts = []
    if blockers or status == "blocker":
        alerts.append(MonitoringAlert("error", "eod_reconciliation", "EOD reconciliation has blocker breaks", {"blockers": blockers}))
    elif errors or status == "error":
        alerts.append(MonitoringAlert("error", "eod_reconciliation", "EOD reconciliation has errors", {"errors": errors}))
    elif status == "warning":
        alerts.append(MonitoringAlert("warning", "eod_reconciliation", "EOD reconciliation has warnings"))
    return {
        "exists": True,
        "eod_reconciliation_status": status,
        "reconciliation_break_count": int(summary.get("break_count", 0) or 0),
        "material_break_count": int(summary.get("material_break_count", 0) or 0),
        "unresolved_break_count": int(summary.get("unresolved_break_count", 0) or 0),
        "unmatched_external_fill_count": int(summary.get("unmatched_external_fill_count", 0) or 0),
        "unmatched_internal_fill_count": int(summary.get("unmatched_internal_fill_count", 0) or 0),
        "fee_tax_difference": float(summary.get("fee_tax_difference", 0.0) or 0.0),
    }, alerts


def check_unresolved_reconciliation_breaks(breaks_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(breaks_path)) if breaks_path else []
    unresolved = [row for row in rows if not row.get("resolved")]
    alerts = [MonitoringAlert("warning", "unresolved_reconciliation_breaks", "unresolved EOD reconciliation breaks exist", {"count": len(unresolved)})] if unresolved else []
    return {"exists": bool(rows), "unresolved_break_count": len(unresolved), "break_count": len(rows)}, alerts


def check_material_reconciliation_breaks(breaks_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(breaks_path)) if breaks_path else []
    material = [row for row in rows if row.get("material")]
    alerts = [MonitoringAlert("error", "material_reconciliation_breaks", "material EOD reconciliation breaks exist", {"count": len(material)})] if material else []
    return {"exists": bool(rows), "material_break_count": len(material)}, alerts


def check_external_cash_difference(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    summary = _eod_summary(report_path)
    diff = float(summary.get("cash_difference", 0.0) or 0.0)
    alerts = [MonitoringAlert("warning", "external_cash_difference", "external cash differs from internal account", {"difference": diff})] if abs(diff) > 0.01 else []
    return {"exists": bool(summary), "external_cash_difference": diff}, alerts


def check_external_position_difference(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    summary = _eod_summary(report_path)
    diff = float(summary.get("position_share_difference", 0.0) or 0.0)
    alerts = [MonitoringAlert("warning", "external_position_difference", "external position shares differ from internal account", {"difference": diff})] if abs(diff) > 0 else []
    return {"exists": bool(summary), "external_position_difference": diff}, alerts


def check_external_nav_difference(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    summary = _eod_summary(report_path)
    diff = float(summary.get("nav_difference", 0.0) or 0.0)
    alerts = [MonitoringAlert("warning", "external_nav_difference", "external equity differs from internal NAV", {"difference": diff})] if abs(diff) > 0.01 else []
    return {"exists": bool(summary), "external_nav_difference": diff}, alerts


def check_adjustment_proposals(proposals_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(proposals_path)) if proposals_path else []
    pending = sum(1 for row in rows if row.get("requires_approval", True))
    alerts = [MonitoringAlert("warning", "adjustment_proposals", "adjustment proposals require approval", {"count": pending})] if pending else []
    return {"exists": bool(rows), "adjustment_proposal_count": len(rows), "adjustment_pending_approval_count": pending}, alerts


def check_adjustment_application(result_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(result_path)) if result_path else {}
    applied = int(payload.get("applied_count", 0) or 0) if payload else 0
    skipped = int(payload.get("skipped_duplicate_count", 0) or 0) if payload else 0
    return {"exists": bool(payload), "adjustment_application_count": applied, "adjustment_skipped_duplicate_count": skipped}, []


def check_data_source_smoke(smoke_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not smoke_report_path:
        return {"exists": False, "provider_status": ""}, []
    payload = _read_json(Path(smoke_report_path))
    status = str(payload.get("status") or "")
    diagnostics = payload.get("diagnostics") or payload.get("provider_probe") or []
    error_count = sum(1 for item in diagnostics if item.get("status") == "ERROR")
    warning_count = sum(1 for item in diagnostics if item.get("status") == "WARNING")
    alerts = []
    if status == "ERROR" or error_count:
        alerts.append(MonitoringAlert("error", "data_source_smoke", "data source smoke report contains errors", {"errors": error_count}))
    elif status == "WARNING" or warning_count:
        alerts.append(MonitoringAlert("warning", "data_source_smoke", "data source smoke report contains warnings", {"warnings": warning_count}))
    return {
        "exists": bool(payload),
        "provider": payload.get("provider", ""),
        "provider_status": status,
        "provider_error_count": error_count,
        "provider_warning_count": warning_count,
        "diagnostic_counts": payload.get("diagnostic_counts", {}),
    }, alerts


def check_provider_readiness(smoke_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(smoke_report_path)) if smoke_report_path else {}
    probes = payload.get("provider_probe") or payload.get("diagnostics") or []
    permission_issues = sum(1 for item in probes if item.get("diagnostic_code") in {"permission_denied", "invalid_token"})
    rate_limit_issues = sum(1 for item in probes if item.get("diagnostic_code") == "rate_limited")
    network_disabled = sum(1 for item in probes if item.get("diagnostic_code") == "network_disabled")
    alerts = []
    if permission_issues:
        alerts.append(MonitoringAlert("error", "provider_readiness", "provider permission diagnostics need review", {"count": permission_issues}))
    if rate_limit_issues:
        alerts.append(MonitoringAlert("warning", "provider_readiness", "provider rate limit diagnostics detected", {"count": rate_limit_issues}))
    return {
        "exists": bool(payload),
        "api_permission_issue_count": permission_issues,
        "rate_limit_issue_count": rate_limit_issues,
        "network_disabled_count": network_disabled,
    }, alerts


def check_field_coverage(field_coverage_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not field_coverage_path:
        return {"exists": False, "missing_field_count": 0, "empty_dataset_count": 0}, []
    payload = _read_json(Path(field_coverage_path))
    datasets = payload.get("datasets", []) if payload else []
    missing = sum(len(item.get("missing_fields", []) or []) for item in datasets)
    empty = sum(1 for item in datasets if int(item.get("records", 0) or 0) == 0)
    duplicate = sum(int(item.get("duplicate_key_count", 0) or 0) for item in datasets)
    alerts = []
    if missing:
        alerts.append(MonitoringAlert("warning", "field_coverage", "field coverage report has missing fields", {"missing_fields": missing}))
    if duplicate:
        alerts.append(MonitoringAlert("error", "field_coverage", "field coverage report has duplicate keys", {"duplicate_keys": duplicate}))
    return {
        "exists": bool(payload),
        "datasets": len(datasets),
        "missing_field_count": missing,
        "empty_dataset_count": empty,
        "duplicate_key_count": duplicate,
    }, alerts


def check_data_source_audit(audit_summary_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not audit_summary_path:
        return {"exists": False, "data_source_cache_hit_rate": 0.0}, []
    payload = _read_json(Path(audit_summary_path))
    failed = int(payload.get("failed_requests", 0) or 0) if payload else 0
    cache_hit_rate = float(payload.get("cache_hit_rate", 0.0) or 0.0) if payload else 0.0
    alerts = []
    if failed:
        alerts.append(MonitoringAlert("error", "data_source_audit", "API audit summary contains failed requests", {"failed_requests": failed}))
    return {
        "exists": bool(payload),
        "total_requests": int(payload.get("total_requests", 0) or 0) if payload else 0,
        "failed_requests": failed,
        "data_source_cache_hit_rate": cache_hit_rate,
        "errors_by_category": payload.get("errors_by_category", {}) if payload else {},
    }, alerts


def check_running_backfill_progress(observer_report_path: str | Path | None, progress_path: str | Path | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(observer_report_path)) if observer_report_path else {}
    rows = _read_jsonl(Path(progress_path)) if progress_path else []
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    active = summary.get("active_backfill_dataset") or ((payload.get("observed_run") or {}).get("active_dataset") if payload else "")
    failed = int(summary.get("failed_jobs", summary.get("backfill_failed_jobs", 0)) or 0) if summary else 0
    remaining = int(summary.get("pending_jobs", summary.get("backfill_remaining_jobs", 0)) or 0) if summary else 0
    alerts = []
    if failed:
        alerts.append(MonitoringAlert("error", "running_backfill_progress", "running backfill has failed jobs", {"failed_jobs": failed}))
    return {
        "exists": bool(payload or rows),
        "active_backfill_dataset": active or "",
        "backfill_progress_ratio": float(summary.get("progress_ratio", summary.get("backfill_progress_ratio", 0.0)) or 0.0) if summary else 0.0,
        "backfill_remaining_jobs": remaining,
        "backfill_failed_jobs": failed,
        "backfill_quarantined_jobs": int(summary.get("quarantined_jobs", summary.get("backfill_quarantined_jobs", 0)) or 0) if summary else 0,
        "dataset_count": len(rows) if rows else int(summary.get("dataset_count", 0) or 0) if summary else 0,
    }, alerts


def check_backfill_eta(eta_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(eta_path)) if eta_path else {}
    minutes = payload.get("estimated_remaining_minutes")
    remaining = int(payload.get("remaining_jobs", 0) or 0) if payload else 0
    return {
        "exists": bool(payload),
        "backfill_eta_minutes": float(minutes) if minutes is not None else None,
        "backfill_remaining_jobs": remaining,
        "eta_confidence": payload.get("confidence", "") if payload else "",
    }, []


def check_backfill_failed_jobs(progress_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(progress_path)) if progress_path else []
    failed = sum(int(row.get("failed_jobs", 0) or 0) for row in rows)
    rate_limit = sum(int(row.get("rate_limit_error_count", 0) or 0) for row in rows)
    permission = sum(int(row.get("permission_error_count", 0) or 0) for row in rows)
    alerts = []
    if failed:
        alerts.append(MonitoringAlert("error", "backfill_failed_jobs", "backfill progress includes failed jobs", {"failed_jobs": failed}))
    return {"exists": bool(rows), "backfill_failed_jobs": failed, "rate_limit_error_count": rate_limit, "permission_error_count": permission}, alerts


def check_backfill_quarantined_jobs(progress_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(progress_path)) if progress_path else []
    quarantined = sum(int(row.get("quarantined_jobs", 0) or 0) for row in rows)
    alerts = [MonitoringAlert("warning", "backfill_quarantined_jobs", "backfill progress includes quarantined jobs", {"quarantined_jobs": quarantined})] if quarantined else []
    return {"exists": bool(rows), "backfill_quarantined_jobs": quarantined}, alerts


def check_backfill_stalled_dataset(observer_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(observer_report_path)) if observer_report_path else {}
    summary = payload.get("summary", {}) if payload else {}
    active = summary.get("active_backfill_dataset") or ((payload.get("observed_run") or {}).get("active_dataset") if payload else "")
    remaining = int(summary.get("backfill_remaining_jobs", 0) or 0) if summary else 0
    stalled = bool(active and remaining and (payload.get("eta") or {}).get("confidence") == "low")
    alerts = [MonitoringAlert("warning", "backfill_stalled_dataset", "backfill ETA confidence is low for active dataset", {"active_dataset": active})] if stalled else []
    return {"exists": bool(payload), "active_backfill_dataset": active or "", "backfill_stalled": stalled}, alerts


def check_raw_data_landing(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    summary = payload.get("summary", {}) if payload else {}
    status = str(summary.get("raw_landing_status") or "")
    alerts = []
    if status == "blocked":
        alerts.append(MonitoringAlert("error", "raw_data_landing", "raw landing report is blocked", summary))
    elif status == "warning":
        alerts.append(MonitoringAlert("warning", "raw_data_landing", "raw landing report has warnings", summary))
    return {
        "exists": bool(payload),
        "raw_landing_status": status,
        "missing_dataset_count": int(summary.get("missing_dataset_count", 0) or 0) if summary else 0,
        "duplicate_key_count": int(summary.get("duplicate_key_count", 0) or 0) if summary else 0,
    }, alerts


def check_raw_data_index(
    manifest_path: str | Path | None,
    report_path: str | Path | None = None,
    validation_report_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    manifest = _read_json(Path(manifest_path)) if manifest_path else {}
    report = _read_json(Path(report_path)) if report_path else {}
    validation = _read_json(Path(validation_report_path)) if validation_report_path else {}
    source = manifest or report
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    status = str((validation or {}).get("status") or source.get("status") or summary.get("raw_data_index_status") or "")
    dataset_count = int(source.get("dataset_count", summary.get("raw_data_index_dataset_count", 0)) or 0) if source else 0
    record_count = int(source.get("total_records", summary.get("raw_data_index_record_count", 0)) or 0) if source else 0
    size_bytes = int(source.get("total_size_bytes", 0) or 0) if source else int(float(summary.get("raw_data_index_size_gb", 0.0) or 0.0) * (1024**3))
    parse_errors = int(source.get("total_parse_errors", summary.get("raw_data_index_parse_error_count", 0)) or 0) if source else 0
    stale = int((validation or {}).get("stale_dataset_count", summary.get("raw_data_index_stale_dataset_count", 0)) or 0)
    missing = int((validation or {}).get("missing_dataset_count", 0) or 0)
    active_blocked = bool(report.get("active_run_blocked", False)) if report else False
    alerts: list[MonitoringAlert] = []
    if status in {"failed", "stale"}:
        alerts.append(MonitoringAlert("warning", "raw_data_index", "raw data index is not fresh", {"status": status, "stale_dataset_count": stale}))
    if parse_errors:
        alerts.append(MonitoringAlert("warning", "raw_data_index", "raw data index captured parse errors", {"parse_error_count": parse_errors}))
    if active_blocked:
        alerts.append(MonitoringAlert("warning", "raw_data_index", "raw data index build was blocked by active run safety"))
    return {
        "exists": bool(source or validation),
        "raw_data_index_status": status,
        "raw_data_index_dataset_count": dataset_count,
        "raw_data_index_record_count": record_count,
        "raw_data_index_size_gb": size_bytes / (1024**3),
        "raw_data_index_parse_error_count": parse_errors,
        "raw_data_index_stale_dataset_count": stale,
        "raw_data_index_missing_core_count": missing,
        "raw_data_index_active_run_blocked": active_blocked,
        "raw_data_index_hash": source.get("index_hash") if source else None,
    }, alerts


def check_data_quality_lab(
    report_path: str | Path | None,
    scorecard_path: str | Path | None = None,
    freeze_gate_path: str | Path | None = None,
    issues_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    report = _read_json(Path(report_path)) if report_path else {}
    scorecard = _read_json(Path(scorecard_path)) if scorecard_path else {}
    freeze_gate = _read_json(Path(freeze_gate_path)) if freeze_gate_path else {}
    issues = _read_jsonl(Path(issues_path)) if issues_path else []
    source_scorecard = scorecard or (report.get("scorecard", {}) if isinstance(report.get("scorecard"), dict) else {})
    source_gate = freeze_gate or (report.get("freeze_gate", {}) if isinstance(report.get("freeze_gate"), dict) else {})
    status = str(source_gate.get("status") or source_scorecard.get("status") or report.get("status") or "")
    blocker_count = int(source_scorecard.get("blocker_count", 0) or 0)
    error_count = int(source_scorecard.get("error_count", 0) or 0)
    warning_count = int(source_scorecard.get("warning_count", 0) or 0)
    core_blockers = int(source_gate.get("core_blocker_count", 0) or 0)
    expanded_blockers = int(source_gate.get("expanded_blocker_count", 0) or 0)
    can_freeze = bool(source_gate.get("can_create_freeze", True)) if source_gate else True
    can_expanded = bool(source_gate.get("can_run_expanded_alpha", True)) if source_gate else True
    alerts: list[MonitoringAlert] = []
    if core_blockers or not can_freeze:
        alerts.append(MonitoringAlert("error", "data_quality_lab", "semantic data quality blocks freeze/matrix/core alpha", {"core_blockers": core_blockers}))
    elif expanded_blockers or not can_expanded:
        alerts.append(MonitoringAlert("warning", "data_quality_lab", "semantic data quality blocks expanded alpha", {"expanded_blockers": expanded_blockers}))
    elif warning_count:
        alerts.append(MonitoringAlert("warning", "data_quality_lab", "semantic data quality has warnings", {"warnings": warning_count}))
    return {
        "exists": bool(report or scorecard or freeze_gate or issues),
        "data_quality_status": status,
        "data_quality_blocker_count": blocker_count,
        "data_quality_error_count": error_count,
        "data_quality_warning_count": warning_count,
        "core_quality_blocker_count": core_blockers,
        "expanded_quality_blocker_count": expanded_blockers,
        "data_quality_issue_count": int(source_scorecard.get("issue_count", len(issues)) or 0),
        "data_quality_can_create_freeze": can_freeze,
        "data_quality_can_build_matrix": bool(source_gate.get("can_build_matrix", True)) if source_gate else True,
        "data_quality_can_run_core_alpha": bool(source_gate.get("can_run_core_alpha", True)) if source_gate else True,
        "data_quality_can_run_expanded_alpha": can_expanded,
        "data_quality_recommended_next_action": str(source_gate.get("recommended_next_action", "")) if source_gate else "",
    }, alerts


def check_data_quality_blockers(scorecard_path: str | Path | None, freeze_gate_path: str | Path | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    return check_data_quality_lab(None, scorecard_path=scorecard_path, freeze_gate_path=freeze_gate_path)


def check_core_dataset_semantic_quality(freeze_gate_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    return check_data_quality_lab(None, freeze_gate_path=freeze_gate_path)


def check_cross_dataset_quality(cross_dataset_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(cross_dataset_report_path)) if cross_dataset_report_path else {}
    mismatches = payload.get("mismatches", {}) if isinstance(payload.get("mismatches"), dict) else {}
    mismatch_count = sum(int(value or 0) for value in mismatches.values())
    alerts = [MonitoringAlert("warning", "cross_dataset_quality", "cross-dataset quality mismatches found", {"mismatch_count": mismatch_count})] if mismatch_count else []
    return {"exists": bool(payload), "cross_dataset_mismatch_count": mismatch_count, "mismatches": mismatches}, alerts


def check_data_quality_freeze_gate(freeze_gate_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    return check_data_quality_lab(None, freeze_gate_path=freeze_gate_path)


def check_raw_freeze_readiness(decision_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(decision_path)) if decision_path else {}
    status = str(payload.get("status") or "")
    blockers = int(payload.get("blocker_count", 0) or 0) if payload else 0
    warnings = int(payload.get("warning_count", 0) or 0) if payload else 0
    alerts = []
    if blockers:
        alerts.append(MonitoringAlert("error", "raw_freeze_readiness", "raw freeze readiness has blockers", {"blockers": blockers}))
    elif warnings:
        alerts.append(MonitoringAlert("warning", "raw_freeze_readiness", "raw freeze readiness has warnings", {"warnings": warnings}))
    return {"exists": bool(payload), "raw_freeze_readiness_status": status, "raw_freeze_blocker_count": blockers, "raw_freeze_warning_count": warnings}, alerts


def check_postprocess_plan_blockers(postprocess_plan_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(postprocess_plan_path)) if postprocess_plan_path else {}
    blockers = len(payload.get("blockers", []) or []) if payload else 0
    alerts = [MonitoringAlert("warning", "postprocess_plan_blockers", "backfill postprocess plan is blocked", {"blockers": blockers})] if blockers else []
    return {"exists": bool(payload), "postprocess_blocker_count": blockers}, alerts


def check_research_data_readiness(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    if payload and (payload.get("artifact_type") == "research_readiness_decision" or "blocker_count" in payload):
        decision = payload
    else:
        decision = payload.get("decision", {}) if payload else {}
    summary = payload.get("summary", {}) if payload else {}
    status = str(decision.get("status") or summary.get("research_data_readiness_status") or "")
    blockers = int(decision.get("blocker_count", summary.get("research_readiness_blocker_count", 0)) or 0) if isinstance(decision, dict) else 0
    alerts = []
    if status in {"failed", "error"}:
        alerts.append(MonitoringAlert("error", "research_data_readiness", "research data readiness report failed", {"status": status, "blockers": blockers}))
    elif status in {"not_ready", "insufficient_data"} or blockers:
        alerts.append(MonitoringAlert("warning", "research_data_readiness", "research data readiness is blocked", {"status": status, "blockers": blockers}))
    elif status not in {"", "ready_for_alpha_factory", "ready_for_validation"}:
        alerts.append(MonitoringAlert("warning", "research_data_readiness", "research data is not yet ready for alpha factory", {"status": status}))
    return {
        "exists": bool(payload),
        "research_data_readiness_status": status,
        "research_readiness_blocker_count": blockers,
        "research_readiness_warning_count": int(decision.get("warning_count", 0) or 0) if isinstance(decision, dict) else 0,
        "alpha_ready": bool(decision.get("alpha_ready", False)) if isinstance(decision, dict) else False,
    }, alerts


def check_feature_readiness(catalog_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(catalog_path)) if catalog_path else {}
    families = payload.get("feature_families", []) if payload else []
    ready = sum(1 for row in families if isinstance(row, dict) and row.get("readiness_status") == "ready")
    blocked = sum(1 for row in families if isinstance(row, dict) and row.get("readiness_status") == "blocked")
    warning = sum(1 for row in families if isinstance(row, dict) and row.get("readiness_status") == "warning")
    alerts = [MonitoringAlert("warning", "feature_readiness", "feature readiness contains blocked families", {"blocked": blocked})] if blocked else []
    return {
        "exists": bool(payload),
        "feature_ready_family_count": ready,
        "feature_blocked_family_count": blocked,
        "feature_warning_family_count": warning,
    }, alerts


def check_post_download_plan(plan_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(plan_path)) if plan_path else {}
    steps = payload.get("steps", []) if payload else []
    blocked = sum(1 for step in steps if isinstance(step, dict) and step.get("blocked"))
    next_step = str(payload.get("next_step") or "") if payload else ""
    alerts = [MonitoringAlert("warning", "post_download_plan", "post-download plan has blocked steps", {"blocked_steps": blocked})] if blocked else []
    return {"exists": bool(payload), "post_download_next_step": next_step, "post_download_blocker_count": blocked}, alerts


def check_post_download_blockers(run_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(run_report_path)) if run_report_path else {}
    summary = payload.get("summary", {}) if payload else {}
    blockers = int(summary.get("post_download_blocker_count", 0) or 0) if isinstance(summary, dict) else 0
    status = str(payload.get("status") or "")
    alerts = (
        [MonitoringAlert("warning", "post_download_blockers", "post-download run is blocked", {"blockers": blockers})]
        if status == "blocked" or blockers
        else []
    )
    return {"exists": bool(payload), "post_download_run_status": status, "post_download_blocker_count": blockers}, alerts


def check_backfill_repair(repair_report_path: str | Path | None, repair_plan_path: str | Path | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    report = _read_json(Path(repair_report_path)) if repair_report_path else {}
    plan = _read_json(Path(repair_plan_path)) if repair_plan_path else {}
    summary = report.get("summary", {}) if report else {}
    plan_summary = plan.get("summary", {}) if plan else {}
    repair_jobs = int(summary.get("repair_job_count", plan_summary.get("repair_job_count", 0)) or 0) if isinstance(summary, dict) else 0
    failed = int(summary.get("failed_jobs", 0) or 0) if isinstance(summary, dict) else 0
    blocked = int(summary.get("blocked_jobs", 0) or 0) if isinstance(summary, dict) else 0
    alerts: list[MonitoringAlert] = []
    if failed:
        alerts.append(MonitoringAlert("error", "repair_failed", "backfill repair has failed jobs", {"failed_jobs": failed}))
    elif blocked:
        alerts.append(MonitoringAlert("warning", "repair_blocked", "backfill repair is blocked", {"blocked_jobs": blocked}))
    elif repair_jobs and not report:
        alerts.append(MonitoringAlert("warning", "repair_required", "backfill repair jobs are planned but not executed", {"repair_job_count": repair_jobs}))
    return {
        "exists": bool(report or plan),
        "repair_job_count": repair_jobs,
        "repair_failed_jobs": failed,
        "repair_blocked_jobs": blocked,
        "repair_run_status": str(report.get("status") or ""),
    }, alerts


def check_post_download_step_runs(step_runs_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(step_runs_path)) if step_runs_path else []
    failed = sum(1 for row in rows if row.get("status") == "failed")
    blocked = sum(1 for row in rows if row.get("status") == "blocked")
    alerts: list[MonitoringAlert] = []
    if failed:
        alerts.append(MonitoringAlert("error", "post_download_step_failed", "post-download step failed", {"failed_steps": failed}))
    if blocked:
        alerts.append(MonitoringAlert("warning", "post_download_blocked", "post-download step blocked", {"blocked_steps": blocked}))
    return {"exists": bool(rows), "post_download_failed_step_count": failed, "post_download_blocked_step_count": blocked}, alerts


def check_freeze_candidate_package(package_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(package_path)) if package_path else {}
    status = str(payload.get("status") or "")
    blockers = payload.get("blockers") if isinstance(payload.get("blockers"), list) else []
    alerts = [MonitoringAlert("warning", "freeze_candidate_blocked", "freeze candidate package is blocked", {"blockers": len(blockers)})] if status == "blocked_candidate" or blockers else []
    return {"exists": bool(payload), "freeze_candidate_status": status, "freeze_candidate_blocker_count": len(blockers)}, alerts


def check_research_readiness_final(decision_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(decision_path)) if decision_path else {}
    status = str(payload.get("status") or (payload.get("decision") or {}).get("status") or "")
    can_alpha = bool(payload.get("can_run_core_alpha_factory", (payload.get("decision") or {}).get("can_run_core_alpha_factory", False)))
    can_matrix = bool(payload.get("can_build_matrix", (payload.get("decision") or {}).get("can_build_matrix", False)))
    alerts: list[MonitoringAlert] = []
    if status and not can_alpha:
        alerts.append(MonitoringAlert("warning", "alpha_factory_not_ready", "Alpha Factory is not ready", {"status": status}))
    if status and not can_matrix:
        alerts.append(MonitoringAlert("warning", "matrix_not_ready", "matrix build is not ready", {"status": status}))
    return {"exists": bool(payload), "final_readiness_status": status, "can_run_core_alpha_factory": can_alpha, "can_build_matrix": can_matrix}, alerts


def check_expanded_dataset_pit_safety(readiness_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(readiness_report_path)) if readiness_report_path else {}
    rows = payload.get("dataset_checks", []) if payload else []
    weak = sum(1 for row in rows if isinstance(row, dict) and row.get("pit_safety") == "weak_pit")
    unsafe = sum(1 for row in rows if isinstance(row, dict) and row.get("pit_safety") == "unsafe_missing_availability")
    alerts = []
    if unsafe:
        alerts.append(MonitoringAlert("warning", "expanded_dataset_pit_safety", "some expanded datasets lack PIT availability fields", {"unsafe": unsafe}))
    return {"exists": bool(payload), "weak_pit_dataset_count": weak, "unsafe_pit_dataset_count": unsafe}, alerts


def check_corporate_action_report(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not report_path:
        return {"exists": False, "corporate_action_event_count": 0}, []
    payload = _read_json(Path(report_path))
    errors = int(payload.get("corporate_action_error_count", 0) or 0) if payload else 0
    warnings = int(payload.get("corporate_action_warning_count", 0) or 0) if payload else 0
    unprocessed = int(payload.get("unprocessed_corporate_action_count", 0) or 0) if payload else 0
    alerts = []
    if errors:
        alerts.append(MonitoringAlert("error", "corporate_action_report", "corporate action report contains errors", {"errors": errors}))
    elif warnings:
        alerts.append(MonitoringAlert("warning", "corporate_action_report", "corporate action report contains warnings", {"warnings": warnings}))
    return {
        "exists": bool(payload),
        "corporate_action_event_count": int(payload.get("event_count", 0) or 0) if payload else 0,
        "implemented_action_count": int(payload.get("implemented_action_count", 0) or 0) if payload else 0,
        "unprocessed_corporate_action_count": unprocessed,
        "corporate_action_error_count": errors,
        "corporate_action_warning_count": warnings,
        "total_return_mode": payload.get("total_return_mode", "") if payload else "",
    }, alerts


def check_total_return_report(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not report_path:
        return {"exists": False, "total_return_records": 0}, []
    payload = _read_json(Path(report_path))
    records = int(payload.get("records", 0) or 0) if payload else 0
    alerts = []
    if payload and records == 0:
        alerts.append(MonitoringAlert("warning", "total_return_report", "total return report has no records"))
    return {
        "exists": bool(payload),
        "total_return_records": records,
        "action_days": int(payload.get("action_days", 0) or 0) if payload else 0,
        "cash_dividend_amount": float(payload.get("cash_dividend_amount", 0.0) or 0.0) if payload else 0.0,
        "stock_distribution_ratio_sum": float(payload.get("stock_distribution_ratio_sum", 0.0) or 0.0) if payload else 0.0,
    }, alerts


def check_corporate_action_ledger(account_dir_or_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not account_dir_or_path:
        return {"exists": False, "corporate_action_ledger_entries": 0}, []
    path = Path(account_dir_or_path)
    ledger_path = path if path.suffix == ".jsonl" else path / "corporate_action_ledger.jsonl"
    rows = _read_jsonl(ledger_path)
    applied = sum(1 for row in rows if row.get("status") == "APPLIED")
    skipped = sum(1 for row in rows if row.get("status") != "APPLIED")
    cash = sum(float(row.get("cash_amount", 0.0) or 0.0) for row in rows if row.get("status") == "APPLIED")
    return {
        "exists": bool(rows),
        "corporate_action_ledger_entries": len(rows),
        "corporate_action_applied_count": applied,
        "corporate_action_skipped_count": skipped,
        "corporate_action_cash_amount": float(cash),
    }, []


def check_baseline_compare(baseline_compare_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not baseline_compare_path:
        return {"exists": False, "baseline_diff_count": 0}, []
    payload = _read_json(Path(baseline_compare_path))
    diff_count = int(payload.get("difference_count", payload.get("diff_count", 0)) or 0) if payload else 0
    alerts = []
    if diff_count:
        alerts.append(MonitoringAlert("warning", "baseline_compare", "baseline comparison contains differences", {"difference_count": diff_count}))
    return {
        "exists": bool(payload),
        "baseline_diff_count": diff_count,
        "has_differences": bool(payload.get("has_differences", False)) if payload else False,
        "metrics": payload.get("metrics", {}) if payload else {},
    }, alerts


def check_backfill_run(backfill_run_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not backfill_run_report_path:
        return {"exists": False, "backfill_status": ""}, []
    payload = _read_json(Path(backfill_run_report_path))
    status = str(payload.get("status") or "")
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    failed = int(summary.get("failed_jobs", 0) or 0)
    alerts = []
    if status in {"failed", "blocked"} or failed:
        alerts.append(MonitoringAlert("error", "backfill_run", "backfill run has failed or blocked jobs", {"failed_jobs": failed}))
    return {
        "exists": bool(payload),
        "backfill_status": status,
        "backfill_failed_jobs": failed,
        "backfill_success_jobs": int(summary.get("success_jobs", 0) or 0),
        "backfill_resumed_jobs": int(summary.get("resumed_jobs", 0) or 0),
        "backfill_records": int(summary.get("records", 0) or 0),
    }, alerts


def check_backfill_coverage(backfill_coverage_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not backfill_coverage_report_path:
        return {"exists": False, "backfill_coverage_gap_count": 0}, []
    payload = _read_json(Path(backfill_coverage_report_path))
    gaps = int(payload.get("gap_count", 0) or 0) if payload else 0
    alerts = []
    if gaps:
        alerts.append(MonitoringAlert("warning", "backfill_coverage", "backfill coverage has gaps", {"gap_count": gaps}))
    return {"exists": bool(payload), "backfill_coverage_gap_count": gaps, "status": payload.get("status", "") if payload else ""}, alerts


def check_data_lake_version(dataset_version_manifest_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not dataset_version_manifest_path:
        return {"exists": False, "dataset_version_id": ""}, []
    payload = _read_json(Path(dataset_version_manifest_path))
    fingerprints = payload.get("dataset_fingerprints", []) if payload else []
    duplicates = sum(int(item.get("duplicate_key_count", 0) or 0) for item in fingerprints if isinstance(item, dict))
    alerts = []
    if duplicates:
        alerts.append(MonitoringAlert("warning", "data_lake_version", "dataset version contains duplicate keys", {"duplicate_key_count": duplicates}))
    return {
        "exists": bool(payload),
        "dataset_version_id": payload.get("dataset_version_id", "") if payload else "",
        "dataset_content_hash": payload.get("content_hash", "") if payload else "",
        "dataset_count": len(fingerprints),
        "duplicate_key_count": duplicates,
    }, alerts


def check_research_freeze(freeze_validation_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not freeze_validation_report_path:
        return {"exists": False, "freeze_validation_status": ""}, []
    payload = _read_json(Path(freeze_validation_report_path))
    status = str(payload.get("status") or "")
    errors = int(payload.get("error_count", 0) or 0) if payload else 0
    warnings = int(payload.get("warning_count", 0) or 0) if payload else 0
    alerts = []
    if errors:
        alerts.append(MonitoringAlert("error", "research_freeze", "research freeze validation has hash drift or missing files", {"error_count": errors}))
    elif warnings:
        alerts.append(MonitoringAlert("warning", "research_freeze", "research freeze validation has warnings", {"warning_count": warnings}))
    return {
        "exists": bool(payload),
        "freeze_id": payload.get("freeze_id") if payload else None,
        "freeze_validation_status": status,
        "data_hash_drift_count": errors,
        "freeze_warning_count": warnings,
    }, alerts


def check_artifact_schema_validation(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not report_path:
        return {"exists": False, "artifact_schema_error_count": 0, "artifact_schema_warning_count": 0}, []
    payload = _read_json(Path(report_path))
    errors = int(payload.get("error_count", 0) or 0) if payload else 0
    warnings = int(payload.get("warning_count", 0) or 0) if payload else 0
    unknown = int(payload.get("unknown_artifact_count", 0) or 0) if payload else 0
    legacy = int(payload.get("legacy_artifact_count", 0) or 0) if payload else 0
    alerts = []
    if errors:
        alerts.append(MonitoringAlert("error", "artifact_schema_validation", "artifact schema validation has errors", {"error_count": errors}))
    elif warnings:
        alerts.append(MonitoringAlert("warning", "artifact_schema_validation", "artifact schema validation has warnings", {"warning_count": warnings}))
    return {
        "exists": bool(payload),
        "artifact_schema_error_count": errors,
        "artifact_schema_warning_count": warnings,
        "unknown_artifact_count": unknown,
        "legacy_artifact_count": legacy,
    }, alerts


def check_release_gate(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not report_path:
        return {"exists": False, "release_gate_status": ""}, []
    payload = _read_json(Path(report_path))
    status = str(payload.get("status") or "")
    checks = payload.get("checks", []) if payload else []
    dirty = any(check.get("name") == "git_clean_check" and check.get("status") == "warning" for check in checks if isinstance(check, dict))
    errors = int(payload.get("error_count", 0) or 0) if payload else 0
    warnings = int(payload.get("warning_count", 0) or 0) if payload else 0
    alerts = []
    if status == "failed" or errors:
        alerts.append(MonitoringAlert("error", "release_gate", "release gate failed", {"error_count": errors}))
    elif warnings:
        alerts.append(MonitoringAlert("warning", "release_gate", "release gate has warnings", {"warning_count": warnings}))
    return {
        "exists": bool(payload),
        "release_gate_status": status,
        "release_gate_errors": errors,
        "release_gate_warnings": warnings,
        "release_dirty_worktree": dirty,
    }, alerts


def check_package_build_artifacts(manifest_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not manifest_path:
        return {"exists": False, "package_build_status": "missing", "build_artifacts": 0}, []
    payload = _read_json(Path(manifest_path))
    build_artifacts = payload.get("build_artifacts", []) if payload else []
    status = "passed" if build_artifacts else "missing"
    alerts = []
    if payload and not build_artifacts:
        alerts.append(MonitoringAlert("warning", "package_build_artifacts", "release manifest has no build artifacts"))
    return {
        "exists": bool(payload),
        "package_build_status": status,
        "build_artifacts": len(build_artifacts),
        "release_name": payload.get("release_name", "") if payload else "",
    }, alerts


def check_formula_corpus(corpus_stats_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not corpus_stats_path:
        return {"exists": False, "valid_records": 0}, []
    payload = _read_json(Path(corpus_stats_path))
    valid = int(payload.get("valid_records", 0) or 0) if payload else 0
    total = int(payload.get("total_records", 0) or 0) if payload else 0
    alerts = [] if valid else [MonitoringAlert("warning", "formula_corpus", "formula corpus has no valid records")]
    return {"exists": bool(payload), "total_records": total, "valid_records": valid}, alerts


def check_formula_batch_eval(result_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not result_path:
        return {"exists": False, "errors": 0}, []
    payload = _read_json(Path(result_path))
    summary = payload.get("summary", {}) if payload else {}
    counts = summary.get("status_counts", {}) if isinstance(summary, dict) else {}
    errors = int(counts.get("error", 0) or 0) if isinstance(counts, dict) else 0
    alerts = []
    if errors:
        alerts.append(MonitoringAlert("warning", "formula_batch_eval", "formula batch evaluation contains errors", {"errors": errors}))
    return {"exists": bool(payload), "status_counts": counts, "errors": errors, "cache_hits": summary.get("cache_hits", 0) if isinstance(summary, dict) else 0}, alerts


def check_alphagpt_pretrain(result_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not result_path:
        return {"exists": False, "status": ""}, []
    payload = _read_json(Path(result_path))
    status = str(payload.get("status") or "") if payload else ""
    latest = ((payload.get("summary") or {}).get("latest_checkpoint_path") if payload else None)
    alerts = []
    if payload and status != "success":
        alerts.append(MonitoringAlert("warning", "alphagpt_pretrain", "AlphaGPT pretrain did not complete successfully", {"status": status}))
    return {"exists": bool(payload), "status": status, "latest_checkpoint_path": latest, "epochs": len(payload.get("history", [])) if payload else 0}, alerts


def check_alphagpt_checkpoint_manifest(manifest_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not manifest_path:
        return {"exists": False, "checkpoints": 0}, []
    payload = _read_json(Path(manifest_path))
    checkpoints = payload.get("checkpoints", []) if payload else []
    latest = payload.get("latest_checkpoint_path", "") if payload else ""
    alerts = []
    if payload and not checkpoints:
        alerts.append(MonitoringAlert("warning", "alphagpt_checkpoint_manifest", "checkpoint manifest has no checkpoints"))
    return {"exists": bool(payload), "checkpoints": len(checkpoints), "latest_checkpoint_path": latest}, alerts


def check_model_registry(registry_dir: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not registry_dir:
        return {"exists": False, "model_versions": 0}, []
    registry = LocalModelRegistry(registry_dir)
    versions = registry.load_model_versions()
    deployments = registry.load_deployments()
    counts: dict[str, int] = {}
    for record in versions:
        counts[record.lifecycle_status] = counts.get(record.lifecycle_status, 0) + 1
    return {
        "exists": bool(versions or deployments),
        "model_versions": len(versions),
        "deployments": len(deployments),
        "status_counts": counts,
        "quarantined_model_count": counts.get("quarantined", 0),
        "paused_model_count": counts.get("paused", 0),
        "retired_model_count": counts.get("retired", 0),
    }, []


def check_active_model_status(
    registry_dir: str | Path | None,
    model_version_id: str | None = None,
) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not registry_dir:
        return {"exists": False, "active_model_version_id": None}, []
    registry = LocalModelRegistry(registry_dir)
    model = registry.get_model_version(model_version_id) if model_version_id else registry.latest_active()
    deployment = registry.latest_active_deployment()
    alerts = []
    if model is None:
        alerts.append(MonitoringAlert("warning", "active_model_status", "no active model found"))
        return {"exists": False, "active_model_version_id": None}, alerts
    if model.lifecycle_status != "active":
        alerts.append(MonitoringAlert("warning", "active_model_status", "selected model is not active", {"status": model.lifecycle_status}))
    return {
        "exists": True,
        "active_model_version_id": model.model_version_id,
        "active_model_factor_id": model.factor_id,
        "model_lifecycle_status": model.lifecycle_status,
        "model_deployment_id": deployment.deployment_id if deployment else "",
    }, alerts


def check_model_lifecycle_health(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    if not payload:
        return {"exists": False, "model_health_error_count": 0, "model_health_warning_count": 0}, []
    evaluation = payload.get("evaluation", {}) if isinstance(payload.get("evaluation"), dict) else {}
    checks = evaluation.get("checks", []) if isinstance(evaluation.get("checks"), list) else []
    errors = sum(1 for check in checks if check.get("severity") in {"error", "blocker"} and not check.get("passed"))
    warnings = sum(1 for check in checks if check.get("severity") == "warning" and not check.get("passed"))
    alerts = []
    if errors:
        alerts.append(MonitoringAlert("error", "model_lifecycle_health", "model lifecycle health has errors", {"errors": errors}))
    elif warnings:
        alerts.append(MonitoringAlert("warning", "model_lifecycle_health", "model lifecycle health has warnings", {"warnings": warnings}))
    return {
        "exists": True,
        "model_health_error_count": errors,
        "model_health_warning_count": warnings,
        "recommended_action": (evaluation.get("decision") or {}).get("recommended_action") if isinstance(evaluation.get("decision"), dict) else "",
    }, alerts


def check_pending_model_reviews(approval_store_dir: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not approval_store_dir:
        return {"exists": False, "pending_model_review_count": 0}, []
    try:
        from approval import LocalApprovalStore

        pending = [
            batch
            for batch in LocalApprovalStore(approval_store_dir).list_batches(status="pending")
            if getattr(batch, "approval_type", "order_batch") == "model_lifecycle"
        ]
    except Exception:
        pending = []
    alerts = [MonitoringAlert("warning", "pending_model_reviews", "pending model lifecycle approvals exist", {"count": len(pending)})] if pending else []
    return {"exists": True, "pending_model_review_count": len(pending)}, alerts


def check_model_lineage_completeness(graph_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(graph_path)) if graph_path else {}
    if not payload:
        return {"exists": False, "model_lineage_node_count": 0, "model_lineage_missing_artifact_count": 0}, []
    warnings = payload.get("warnings", []) if isinstance(payload.get("warnings"), list) else []
    return {
        "exists": True,
        "model_lineage_node_count": len(payload.get("nodes", []) or []),
        "model_lineage_edge_count": len(payload.get("edges", []) or []),
        "model_lineage_missing_artifact_count": len(warnings),
    }, []


def check_model_rollback_state(registry_dir: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not registry_dir:
        return {"exists": False, "model_rollback_available": False}, []
    registry = LocalModelRegistry(registry_dir)
    previous = [deployment for deployment in registry.load_deployments() if deployment.status == "previous"]
    return {"exists": True, "model_rollback_available": bool(previous), "previous_deployments": len(previous)}, []


def check_quarantined_or_paused_model_usage(registry_dir: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    if not registry_dir:
        return {"exists": False, "quarantined_model_count": 0, "paused_model_count": 0}, []
    versions = LocalModelRegistry(registry_dir).load_model_versions()
    paused = sum(1 for record in versions if record.lifecycle_status == "paused")
    quarantined = sum(1 for record in versions if record.lifecycle_status == "quarantined")
    alerts = []
    if paused or quarantined:
        alerts.append(MonitoringAlert("warning", "model_lifecycle_status", "paused or quarantined models exist", {"paused": paused, "quarantined": quarantined}))
    return {"exists": bool(versions), "paused_model_count": paused, "quarantined_model_count": quarantined}, alerts


def check_order_fill_quality(fills_path: str | Path) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    fills = _read_jsonl(Path(fills_path))
    total = len(fills)
    rejected = sum(1 for fill in fills if fill.get("status") == "REJECTED")
    partial = sum(1 for fill in fills if fill.get("status") == "PARTIAL")
    reject_ratio = rejected / total if total else 0.0
    partial_ratio = partial / total if total else 0.0
    alerts = []
    if reject_ratio > 0.5:
        alerts.append(MonitoringAlert("warning", "fill_quality", "rejected fill ratio is high", {"reject_ratio": reject_ratio}))
    if partial_ratio > 0.5:
        alerts.append(MonitoringAlert("warning", "fill_quality", "partial fill ratio is high", {"partial_ratio": partial_ratio}))
    return {"fills": total, "rejected": rejected, "partial": partial, "reject_ratio": reject_ratio, "partial_ratio": partial_ratio}, alerts


def check_paper_account(account_dir: str | Path) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    state = LocalPaperAccount(account_dir).load_state()
    performance = compute_account_performance(state)
    alerts = []
    if performance.get("cash_ratio", 0.0) > 0.95 and state.positions:
        alerts.append(MonitoringAlert("warning", "paper_account", "cash ratio is high despite positions", {"cash_ratio": performance["cash_ratio"]}))
    if performance.get("max_drawdown", 0.0) > 0.2:
        alerts.append(MonitoringAlert("warning", "paper_account", "paper account drawdown is elevated", {"max_drawdown": performance["max_drawdown"]}))
    return {
        "account_id": state.account_id,
        "cash": state.cash,
        "positions": len(state.positions),
        "snapshots": len(state.snapshots),
        "performance": performance,
    }, alerts


def check_point_in_time_validation(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    blockers = int(payload.get("blocker_count", 0) or 0) if payload else 0
    warnings = int(payload.get("warning_count", 0) or 0) if payload else 0
    alerts = []
    if blockers:
        alerts.append(MonitoringAlert("error", "point_in_time_validation", "PIT validation has blockers", {"blocker_count": blockers}))
    elif warnings:
        alerts.append(MonitoringAlert("warning", "point_in_time_validation", "PIT validation has warnings", {"warning_count": warnings}))
    return {
        "exists": bool(payload),
        "pit_blocker_count": blockers,
        "pit_warning_count": warnings,
        "status": payload.get("status", "") if payload else "",
        "active_universe_coverage": float(payload.get("active_universe_coverage", 0.0) or 0.0) if payload else 0.0,
    }, alerts


def check_survivorship_bias(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    current_only = bool(payload.get("current_only_security_master", False)) if payload else False
    warnings = int(payload.get("warning_count", 0) or 0) if payload else 0
    alerts = [MonitoringAlert("warning", "survivorship_bias", "security master appears current-only")] if current_only else []
    return {
        "exists": bool(payload),
        "current_only_security_master": current_only,
        "survivorship_warning_count": warnings,
        "delisted_count": int(payload.get("delisted_count", 0) or 0) if payload else 0,
        "paused_count": int(payload.get("paused_count", 0) or 0) if payload else 0,
    }, alerts


def check_leakage_audit(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    blockers = int(payload.get("blocker_count", 0) or 0) if payload else 0
    warnings = int(payload.get("warning_count", 0) or 0) if payload else 0
    alerts = []
    if blockers:
        alerts.append(MonitoringAlert("error", "leakage_audit", "leakage audit has blockers", {"blocker_count": blockers}))
    elif warnings:
        alerts.append(MonitoringAlert("warning", "leakage_audit", "leakage audit has warnings", {"warning_count": warnings}))
    return {
        "exists": bool(payload),
        "leakage_blocker_count": blockers,
        "leakage_warning_count": warnings,
        "leakage_gate_status": payload.get("leakage_gate_status", "") if payload else "",
    }, alerts


def check_truncation_consistency(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    if not payload:
        return {"exists": False, "truncation_consistency_passed": None, "truncation_max_abs_diff": 0.0}, []
    passed = bool(payload.get("passed", True)) if payload else False
    max_diff = float(payload.get("max_abs_diff", 0.0) or 0.0) if payload else 0.0
    alerts = [] if passed else [MonitoringAlert("error", "truncation_consistency", "truncation consistency failed", {"max_abs_diff": max_diff})]
    return {"exists": bool(payload), "truncation_consistency_passed": passed, "truncation_max_abs_diff": max_diff}, alerts


def check_active_universe_coverage(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    coverage = float(payload.get("active_universe_coverage", 0.0) or 0.0) if payload else 0.0
    alerts = []
    if payload and coverage <= 0:
        alerts.append(MonitoringAlert("error", "active_universe_coverage", "active universe coverage is zero"))
    return {"exists": bool(payload), "active_universe_coverage": coverage}, alerts


def check_feature_cutoff_policy(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    mode = str(payload.get("feature_cutoff_mode") or "") if payload else ""
    alerts = []
    if payload and mode == "same_day_after_close":
        alerts.append(MonitoringAlert("info", "feature_cutoff_policy", "same_day_after_close mode requires execution timing review"))
    return {"exists": bool(payload), "feature_cutoff_mode": mode}, alerts


def check_settlement_report(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    if not payload:
        return {"exists": False, "pending_settlement_event_count": 0, "failed_settlement_event_count": 0}, []
    pending = int(payload.get("pending_settlement_event_count", 0) or 0)
    failed = int(payload.get("failed_settlement_event_count", 0) or 0)
    nav_difference = abs(float(payload.get("nav_difference", 0.0) or 0.0))
    errors = int(payload.get("reconciliation_error_count", 0) or 0)
    alerts: list[MonitoringAlert] = []
    if failed or errors:
        alerts.append(
            MonitoringAlert(
                "error",
                "settlement_report",
                "settlement report contains failed events or reconciliation errors",
                {"failed_events": failed, "reconciliation_errors": errors},
            )
        )
    elif pending:
        alerts.append(MonitoringAlert("info", "settlement_report", "pending settlement events exist", {"pending": pending}))
    if nav_difference > 1e-6:
        alerts.append(MonitoringAlert("warning", "settlement_nav", "settlement NAV reconciliation difference detected", {"nav_difference": nav_difference}))
    return {
        "exists": True,
        "pending_settlement_event_count": pending,
        "failed_settlement_event_count": failed,
        "settlement_reconciliation_error_count": errors,
        "nav_difference": nav_difference,
        "realized_pnl": float(payload.get("realized_pnl", 0.0) or 0.0),
        "unrealized_pnl": float(payload.get("unrealized_pnl", 0.0) or 0.0),
        "fee_tax_total": float(payload.get("fee_tax_total", 0.0) or 0.0),
    }, alerts


def check_account_reconciliation(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    if not payload:
        return {"exists": False, "error_count": 0}, []
    errors = int(payload.get("error_count", 0) or 0)
    warnings = int(payload.get("warning_count", 0) or 0)
    alerts = []
    if errors:
        alerts.append(MonitoringAlert("error", "account_reconciliation", "account reconciliation has errors", {"error_count": errors}))
    elif warnings:
        alerts.append(MonitoringAlert("warning", "account_reconciliation", "account reconciliation has warnings", {"warning_count": warnings}))
    return {
        "exists": True,
        "error_count": errors,
        "warning_count": warnings,
        "cash_difference": float(payload.get("cash_difference", 0.0) or 0.0),
        "lot_share_difference": int(payload.get("lot_share_difference", 0) or 0),
        "nav_difference": float(payload.get("nav_difference", 0.0) or 0.0),
    }, alerts


def check_settlement_fee_tax(fee_tax_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(fee_tax_report_path)) if fee_tax_report_path else {}
    if not payload:
        return {"exists": False, "total_fee_tax": 0.0}, []
    total = float(payload.get("total_fee_tax", payload.get("fee_tax_total", 0.0)) or 0.0)
    alerts = []
    if total < 0:
        alerts.append(MonitoringAlert("error", "settlement_fee_tax", "fee and tax total is negative"))
    return {"exists": True, "total_fee_tax": total, **payload}, alerts


def check_alpha_factory_campaign(report_path: str | Path | None, manifest_path: str | Path | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    report = _read_json(Path(report_path)) if report_path else {}
    manifest = _read_json(Path(manifest_path)) if manifest_path else {}
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    status = str(report.get("status", "missing") if report else "missing")
    alerts: list[MonitoringAlert] = []
    if report and status not in {"success", "partial"}:
        alerts.append(MonitoringAlert("warning", "alpha_factory_campaign", "alpha campaign did not finish successfully", {"status": status}))
    return {
        "exists": bool(report or manifest),
        "alpha_campaign_id": report.get("campaign_id") or manifest.get("campaign_id"),
        "alpha_status": status,
        "alpha_candidates_generated": int(summary.get("candidates_generated", 0) or 0),
        "alpha_static_pass_count": int(summary.get("static_passed", 0) or 0),
        "alpha_static_error_count": int(summary.get("static_error_count", 0) or 0),
        "alpha_proxy_pass_count": int(summary.get("proxy_passed", 0) or 0),
        "alpha_full_eval_count": int(summary.get("full_eval_count", 0) or 0),
        "alpha_shortlist_count": int(summary.get("shortlist_count", 0) or 0),
        "alpha_best_score": float(summary.get("best_score", 0.0) or 0.0),
        "alpha_feature_count": int(summary.get("feature_count", 0) or 0),
        "alpha_family_count": len(summary.get("family_distribution", {}) or {}),
        "alpha_compute_run_report_path": summary.get("compute_run_report_path"),
    }, alerts


def check_alpha_static_errors(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(path)) if path else []
    errors = sum(1 for row in rows if row.get("status") != "passed")
    alerts = [MonitoringAlert("warning", "alpha_static_errors", "alpha static checks rejected candidates", {"errors": errors})] if errors else []
    return {"exists": bool(rows), "alpha_static_error_count": errors, "alpha_static_pass_count": len(rows) - errors}, alerts


def check_alpha_proxy_eval(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    return {"exists": bool(payload), "alpha_proxy_pass_count": int(summary.get("passed", 0) or 0), "alpha_proxy_failed_count": int(summary.get("failed", 0) or 0)}, []


def check_alpha_diversity(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    warnings = int(payload.get("warning_count", 0) or 0) if payload else 0
    return {
        "exists": bool(payload),
        "alpha_shortlist_count": int(payload.get("shortlist_count", 0) or 0) if payload else 0,
        "alpha_diversity_warning_count": warnings,
        "alpha_family_count": len(payload.get("family_counts", {}) or {}) if payload else 0,
    }, []


def check_alpha_shortlist(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(path)) if path else []
    best = max([float(row.get("final_score", 0.0) or 0.0) for row in rows], default=0.0)
    return {"exists": bool(rows), "alpha_shortlist_count": len(rows), "alpha_best_score": best}, []


def check_alpha_experiment_store(report_path: str | Path | None, registry_path: str | Path | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    report = _read_json(Path(report_path)) if report_path else {}
    registry = _read_json(Path(registry_path)) if registry_path else {}
    payload = report or registry
    failed = int(payload.get("failed_shard_count", 0) or 0)
    leaderboard = int(payload.get("leaderboard_count", 0) or 0)
    validation_count = int(payload.get("validation_candidate_count", 0) or 0)
    alerts: list[MonitoringAlert] = []
    if failed:
        alerts.append(MonitoringAlert("warning", "alpha_shard_failures", "alpha experiment store has failed shards", {"failed_shard_count": failed}))
    if payload and leaderboard == 0:
        alerts.append(MonitoringAlert("warning", "alpha_leaderboard_empty", "alpha leaderboard is empty"))
    return {
        "exists": bool(payload),
        "alpha_experiment_store_status": payload.get("status", "missing") if payload else "missing",
        "alpha_experiment_count": int(payload.get("experiment_count", 0) or 0),
        "alpha_shard_count": int(payload.get("shard_count", 0) or 0),
        "alpha_failed_shard_count": failed,
        "alpha_consolidated_factor_count": int(payload.get("consolidated_factor_count", 0) or 0),
        "alpha_leaderboard_count": leaderboard,
        "alpha_validation_candidate_count": validation_count,
    }, alerts


def check_alpha_dedup_report(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    conflicts = int(payload.get("conflict_count", 0) or 0) if payload else 0
    duplicates = int(payload.get("duplicate_count", 0) or 0) if payload else 0
    alerts = [MonitoringAlert("warning", "alpha_dedup_conflicts", "alpha factor dedup had conflicts", {"conflict_count": conflicts})] if conflicts else []
    return {
        "exists": bool(payload),
        "alpha_dedup_conflict_count": conflicts,
        "alpha_duplicate_count": duplicates,
        "alpha_merged_factor_count": int(payload.get("merged_factor_count", 0) or 0) if payload else 0,
    }, alerts


def check_alpha_validation_pool(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(path)) if path else []
    alerts = [] if rows else [MonitoringAlert("warning", "alpha_validation_pool_ready", "alpha validation candidate pool is empty")]
    return {"exists": bool(rows), "alpha_validation_candidate_count": len(rows), "alpha_validation_pool_ready": bool(rows)}, alerts


def check_alpha_large_campaign_plan(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    blocked = bool(payload.get("blocked", False)) if payload else False
    alerts = []
    if blocked:
        alerts.append(MonitoringAlert("info", "alpha_large_campaign_blocked_by_readiness", "large alpha campaign plan is blocked by readiness"))
    return {
        "exists": bool(payload),
        "alpha_large_campaign_status": payload.get("status", "missing") if payload else "missing",
        "alpha_large_campaign_blocked": blocked,
        "alpha_large_campaign_shard_count": int(payload.get("shard_count", 0) or 0) if payload else 0,
        "alpha_large_campaign_gpu_count": int(payload.get("gpu_count_requested", 0) or 0) if payload else 0,
    }, alerts


def check_validation_campaign_store(
    report_path: str | Path | None,
    registry_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    report = _read_json(Path(report_path)) if report_path else {}
    registry = _read_json(Path(registry_path)) if registry_path else {}
    payload = report or registry
    status = str(payload.get("status", "missing") if payload else "missing")
    failed_shards = int(payload.get("failed_shard_count", 0) or 0) if payload else 0
    result_count = int(payload.get("result_count", 0) or 0) if payload else 0
    blocker_count = int(((payload.get("summary") or {}).get("validation_blocker_count", 0)) if isinstance(payload.get("summary"), dict) else 0)
    alerts: list[MonitoringAlert] = []
    if failed_shards:
        alerts.append(MonitoringAlert("warning", "validation_shard_failures", "validation campaign has failed shards", {"failed_shard_count": failed_shards}))
    if status in {"blocked", "failed", "error"}:
        alerts.append(MonitoringAlert("warning", "validation_campaign_status", "validation campaign is not ready", {"status": status}))
    return {
        "exists": bool(payload),
        "validation_campaign_status": status,
        "validation_campaign_count": int(payload.get("validation_campaign_count", 0) or 0) if payload else 0,
        "validation_candidate_count": int(payload.get("candidate_count", 0) or 0) if payload else 0,
        "validation_shard_count": int(payload.get("shard_count", 0) or 0) if payload else 0,
        "validation_failed_shard_count": failed_shards,
        "validation_result_count": result_count,
        "validation_campaign_blocker_count": blocker_count,
    }, alerts


def check_validation_campaign_leaderboard(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(path)) if path else []
    ready = sum(1 for row in rows if row.get("certification_ready") is True)
    alerts = [] if rows else [MonitoringAlert("warning", "validation_leaderboard_empty", "validation campaign leaderboard is empty")]
    return {"exists": bool(rows), "validation_leaderboard_count": len(rows), "certification_ready_count": ready}, alerts


def check_factor_certification_queue(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(path)) if path else []
    alerts = [] if rows else [MonitoringAlert("warning", "certification_queue_empty", "factor certification queue is empty")]
    return {"exists": bool(rows), "certification_queue_count": len(rows), "queued_factor_count": len({row.get("factor_id") for row in rows})}, alerts


def check_validation_large_campaign_plan(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    blocked = bool(payload.get("blocked", False)) if payload else False
    alerts = []
    if blocked:
        alerts.append(MonitoringAlert("info", "validation_campaign_blocked_by_readiness", "large validation campaign plan is blocked by readiness"))
    return {
        "exists": bool(payload),
        "validation_large_campaign_status": payload.get("status", "missing") if payload else "missing",
        "validation_large_campaign_blocked": blocked,
        "validation_large_campaign_shard_count": int(payload.get("shard_count", 0) or 0) if payload else 0,
        "validation_large_campaign_compute_jobs": len(payload.get("compute_jobs", []) or []) if payload else 0,
    }, alerts


def check_factor_certification_campaign(
    report_path: str | Path | None,
    registry_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    report = _read_json(Path(report_path)) if report_path else {}
    registry = _read_json(Path(registry_path)) if registry_path else {}
    payload = report or registry
    status = str(payload.get("status", "missing") if payload else "missing")
    failed = int(payload.get("failed_item_count", 0) or 0) if payload else 0
    pool_count = int(payload.get("certified_factor_pool_count", 0) or 0) if payload else 0
    alerts: list[MonitoringAlert] = []
    if failed:
        alerts.append(MonitoringAlert("warning", "factor_certification_campaign_status", "factor certification campaign has failed items", {"failed_item_count": failed}))
    if payload and pool_count == 0:
        alerts.append(MonitoringAlert("warning", "certified_factor_pool_empty", "certified factor pool is empty"))
    return {
        "exists": bool(payload),
        "factor_certification_campaign_status": status,
        "factor_certification_campaign_item_count": int(payload.get("item_count", 0) or 0) if payload else 0,
        "factor_certification_campaign_failed_item_count": failed,
        "certified_factor_pool_count": pool_count,
    }, alerts


def check_certified_factor_pool(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(path)) if path else []
    alerts = [] if rows else [MonitoringAlert("warning", "certified_factor_pool_empty", "certified factor pool is empty")]
    return {"exists": bool(rows), "certified_factor_pool_count": len(rows), "certified_factor_count": len({row.get("factor_id") for row in rows})}, alerts


def check_portfolio_campaign(
    report_path: str | Path | None,
    registry_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    report = _read_json(Path(report_path)) if report_path else {}
    registry = _read_json(Path(registry_path)) if registry_path else {}
    payload = report or registry
    status = str(payload.get("status", "missing") if payload else "missing")
    failed = int(payload.get("failed_item_count", 0) or 0) if payload else 0
    bundle_count = int(payload.get("production_candidate_bundle_count", 0) or 0) if payload else 0
    alerts: list[MonitoringAlert] = []
    if failed:
        alerts.append(MonitoringAlert("warning", "portfolio_campaign_status", "portfolio campaign has failed items", {"failed_item_count": failed}))
    if payload and bundle_count == 0:
        alerts.append(MonitoringAlert("warning", "production_candidate_bundle_empty", "production candidate bundle is empty"))
    return {
        "exists": bool(payload),
        "portfolio_campaign_status": status,
        "portfolio_campaign_item_count": int(payload.get("item_count", 0) or 0) if payload else 0,
        "portfolio_campaign_failed_item_count": failed,
        "production_candidate_bundle_count": bundle_count,
        "optimizer_policy_activation_queue_count": int(payload.get("optimizer_policy_activation_queue_count", 0) or 0) if payload else 0,
    }, alerts


def check_production_candidate_bundle(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(path)) if path else []
    alerts = [] if rows else [MonitoringAlert("warning", "production_candidate_bundle_empty", "production candidate bundle is empty")]
    pending = sum(1 for row in rows if row.get("selected_for_activation_review") is True)
    return {"exists": bool(rows), "production_candidate_bundle_count": len(rows), "activation_review_candidate_count": pending}, alerts


def check_optimizer_policy_activation_queue(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(path)) if path else []
    pending = sum(1 for row in rows if str(row.get("status")) in {"pending", "pending_review", "queued"})
    alerts = []
    if pending:
        alerts.append(MonitoringAlert("info", "optimizer_policy_activation_queue_pending", "optimizer policy activation queue has pending review items", {"pending_count": pending}))
    return {"exists": bool(rows), "optimizer_policy_activation_queue_count": len(rows), "optimizer_policy_activation_pending_count": pending}, alerts


def check_feature_set_manifest(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    return {
        "exists": bool(payload),
        "feature_set_name": payload.get("feature_set_name"),
        "feature_count": int(payload.get("feature_count", 0) or 0) if payload else 0,
        "feature_set_hash": payload.get("content_hash"),
    }, []


def check_feature_coverage(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    warnings = len(payload.get("warnings", []) or []) if payload else 0
    alerts = [MonitoringAlert("warning", "feature_coverage", "feature coverage warnings exist", {"warnings": warnings})] if warnings else []
    return {"exists": bool(payload), "feature_coverage_warning_count": warnings}, alerts


def check_feature_set_v3(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    name = str(payload.get("feature_set_name") or "")
    definitions = payload.get("feature_definitions", []) if isinstance(payload.get("feature_definitions"), list) else []
    weak = sum(1 for item in definitions if isinstance(item, dict) and item.get("pit_safety") != "pit_safe")
    disabled = sum(1 for item in definitions if isinstance(item, dict) and not item.get("default_enabled", True))
    alerts = []
    if payload and name != "ashare_features_v3":
        alerts.append(MonitoringAlert("info", "feature_set_v3", "feature set manifest is not v3", {"feature_set_name": name}))
    return {
        "exists": bool(payload),
        "feature_set_name": name,
        "v3_feature_count": len(definitions),
        "v3_weak_pit_feature_count": weak,
        "v3_disabled_feature_count": disabled,
    }, alerts


def check_v3_feature_family_readiness(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    families = payload.get("families", []) if isinstance(payload.get("families"), list) else []
    insufficient = [row for row in families if row.get("readiness") == "insufficient_data"]
    warning = [row for row in families if row.get("readiness") == "warning"]
    alerts: list[MonitoringAlert] = []
    if insufficient:
        alerts.append(MonitoringAlert("warning", "v3_feature_family_readiness", "some v3 feature families have insufficient data", {"families": [row.get("family") for row in insufficient]}))
    elif warning:
        alerts.append(MonitoringAlert("info", "v3_feature_family_readiness", "some v3 feature families have warnings", {"families": [row.get("family") for row in warning]}))
    return {
        "exists": bool(payload),
        "v3_feature_family_count": len(families),
        "v3_feature_family_ready_count": sum(1 for row in families if row.get("readiness") == "ready"),
        "v3_feature_family_warning_count": len(warning),
        "v3_feature_family_insufficient_count": len(insufficient),
    }, alerts


def check_weak_pit_features(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    weak = int(payload.get("weak_pit_feature_count", 0) or 0) if payload else 0
    disabled = int(payload.get("disabled_feature_count", 0) or 0) if payload else 0
    alerts = []
    if weak:
        alerts.append(MonitoringAlert("info", "weak_pit_features", "weak PIT features are present and should stay excluded from default alpha sampling", {"weak_pit_feature_count": weak}))
    return {"exists": bool(payload), "weak_pit_feature_count": weak, "disabled_feature_count": disabled}, alerts


def check_feature_pit_alignment(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    status = str(payload.get("status") or "")
    features = payload.get("features", []) if isinstance(payload.get("features"), list) else []
    warning_count = sum(1 for row in features if str(row.get("status", "")).endswith("warning"))
    alerts = [MonitoringAlert("warning", "feature_pit_alignment", "feature PIT alignment has warnings", {"warning_count": warning_count})] if warning_count else []
    return {"exists": bool(payload), "feature_pit_alignment_status": status, "feature_pit_alignment_warning_count": warning_count}, alerts


def check_alpha_factory_v3_readiness(feature_family_readiness_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(feature_family_readiness_path)) if feature_family_readiness_path else {}
    families = payload.get("families", []) if isinstance(payload.get("families"), list) else []
    ready = {row.get("family") for row in families if row.get("readiness") == "ready"}
    can_run = bool(ready & {"moneyflow", "margin", "financial_statement", "industry", "index_market"})
    alerts = [] if can_run or not payload else [MonitoringAlert("warning", "alpha_factory_v3_readiness", "v3 expanded alpha factory is blocked by feature family readiness")]
    return {"exists": bool(payload), "can_run_v3_expanded_alpha_factory": can_run, "ready_v3_families": sorted(str(item) for item in ready)}, alerts


def check_feature_promotion_policy(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    policy_hash = str(payload.get("policy_hash") or (payload.get("metadata") or {}).get("policy_hash") or "")
    status = "configured" if payload else "missing"
    alerts = []
    if not payload:
        alerts.append(MonitoringAlert("info", "feature_promotion_policy", "feature promotion policy artifact is missing"))
    return {
        "exists": bool(payload),
        "feature_promotion_status": status,
        "feature_promotion_policy_hash": policy_hash,
        "feature_promotion_feature_set": str(payload.get("feature_set_name", "")),
    }, alerts


def check_unreviewed_weak_pit_features(evidence_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(evidence_report_path)) if evidence_report_path else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else payload
    weak = int(summary.get("weak_pit_feature_count", 0) or 0) if summary else 0
    needs_review = int(summary.get("needs_review_count", summary.get("review_required_count", 0)) or 0) if summary else 0
    alerts = []
    if needs_review:
        alerts.append(MonitoringAlert("warning", "unreviewed_weak_pit_features", "weak PIT features still need promotion review", {"needs_review_count": needs_review}))
    return {
        "exists": bool(payload),
        "weak_pit_unreviewed_count": needs_review,
        "weak_pit_feature_count": weak,
    }, alerts


def check_blocked_features_used(application_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(application_report_path)) if application_report_path else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else payload
    blocked = int(summary.get("blocked_feature_count", 0) or summary.get("denylist_count", 0) or 0) if summary else 0
    unapproved = int(summary.get("unapproved_feature_usage_count", 0) or 0) if summary else 0
    alerts = []
    if unapproved:
        alerts.append(MonitoringAlert("error", "blocked_features_used", "unapproved feature usage was detected", {"count": unapproved}))
    return {
        "exists": bool(payload),
        "blocked_feature_count": blocked,
        "unapproved_feature_usage_count": unapproved,
    }, alerts


def check_feature_promotion_expiry(decisions_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(decisions_path)) if decisions_path else []
    expired = [row for row in rows if str(row.get("status", "")).lower() == "expired"]
    alerts = []
    if expired:
        alerts.append(MonitoringAlert("warning", "feature_promotion_expiry", "feature promotion decisions expired", {"count": len(expired)}))
    return {"exists": bool(rows), "expired_promotion_count": len(expired)}, alerts


def check_feature_promotion_approval(review_package_path: str | Path | None, allowlist_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    review = _read_json(Path(review_package_path)) if review_package_path else {}
    allowlist = _read_json(Path(allowlist_path)) if allowlist_path else {}
    alpha = len(allowlist.get("alpha_eligible_features", []) or []) if allowlist else 0
    risk = len(allowlist.get("risk_filter_only_features", []) or []) if allowlist else 0
    promoted_weak = len(allowlist.get("promoted_weak_pit_features", []) or []) if allowlist else 0
    alerts = []
    if review and not allowlist:
        alerts.append(MonitoringAlert("warning", "feature_promotion_approval", "feature promotion review exists but allowlist is missing"))
    return {
        "exists": bool(review or allowlist),
        "alpha_eligible_feature_count": alpha,
        "risk_filter_feature_count": risk,
        "promoted_weak_pit_count": promoted_weak,
    }, alerts


def check_validation_lab(
    path: str | Path | None,
    factor_validation_summary_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    summary = payload.get("validation_summary", {}) if isinstance(payload.get("validation_summary"), dict) else {}
    if not summary and factor_validation_summary_path:
        summary = _read_json(Path(factor_validation_summary_path))
    blocker_count = int(summary.get("blocker_count", 0) or 0) if summary else 0
    warning_count = int(summary.get("warning_count", 0) or 0) if summary else 0
    status = str(payload.get("status", "missing") if payload else "missing")
    if status == "missing" and summary:
        status = str(summary.get("status", "summary_only"))
    alerts = []
    if blocker_count:
        alerts.append(MonitoringAlert("error", "validation_lab", "validation blockers exist", {"blocker_count": blocker_count}))
    elif warning_count:
        alerts.append(MonitoringAlert("warning", "validation_lab", "validation warnings exist", {"warning_count": warning_count}))
    return {
        "exists": bool(payload),
        "validation_status": status,
        "validation_blocker_count": blocker_count,
        "validation_warning_count": warning_count,
        "out_of_sample_score": float(summary.get("out_of_sample_score", 0.0) or 0.0) if summary else 0.0,
        "window_pass_ratio": float(summary.get("window_pass_ratio", 0.0) or 0.0) if summary else 0.0,
    }, alerts


def check_multiple_testing(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    warning = bool(payload.get("selection_bias_warning", False)) if payload else False
    alerts = [MonitoringAlert("warning", "multiple_testing", "selection bias warning is active")] if warning else []
    return {
        "exists": bool(payload),
        "effective_trial_count": int(payload.get("effective_trial_count", 0) or 0) if payload else 0,
        "selection_bias_warning": warning,
    }, alerts


def check_overfit_risk(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    pbo = float(payload.get("pbo_estimate", 0.0) or 0.0) if payload else 0.0
    level = str(payload.get("overfit_risk_level", "") if payload else "")
    alerts = [MonitoringAlert("warning", "overfit_risk", "overfit risk is elevated", {"pbo": pbo})] if level == "high" else []
    return {
        "exists": bool(payload),
        "pbo_estimate": pbo,
        "deflated_ic_score": float(payload.get("deflated_ic_like_score", 0.0) or 0.0) if payload else 0.0,
        "overfit_risk_level": level,
    }, alerts


def check_placebo_tests(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    percentile = float(payload.get("candidate_vs_placebo_percentile", 0.0) or 0.0) if payload else 0.0
    null_ratio = float(payload.get("null_exceedance_ratio", 0.0) or 0.0) if payload else 0.0
    alerts = [MonitoringAlert("warning", "placebo_tests", "candidate does not beat placebo strongly", {"percentile": percentile})] if payload and percentile < 0.5 else []
    return {"exists": bool(payload), "placebo_percentile": percentile, "null_exceedance_ratio": null_ratio}, alerts


def check_regime_validation(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    ratio = float(payload.get("regime_pass_ratio", 0.0) or 0.0) if payload else 0.0
    alerts = [MonitoringAlert("warning", "regime_validation", "regime pass ratio is low", {"ratio": ratio})] if payload and ratio < 0.5 else []
    return {"exists": bool(payload), "regime_pass_ratio": ratio, "regime_count": int(payload.get("regime_count", 0) or 0) if payload else 0}, alerts


def check_sensitivity_validation(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    ratio = float(payload.get("sensitivity_pass_ratio", 0.0) or 0.0) if payload else 0.0
    alerts = [MonitoringAlert("warning", "sensitivity_validation", "sensitivity pass ratio is low", {"ratio": ratio})] if payload and ratio < 0.5 else []
    return {"exists": bool(payload), "sensitivity_pass_ratio": ratio, "scenario_count": int(payload.get("scenario_count", 0) or 0) if payload else 0}, alerts


def check_stress_backtest_validation(path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    ratio = float(payload.get("stress_backtest_pass_ratio", 0.0) or 0.0) if payload else 0.0
    alerts = [MonitoringAlert("warning", "stress_backtest_validation", "stress backtest pass ratio is low", {"ratio": ratio})] if payload and ratio < 0.5 else []
    return {"exists": bool(payload), "stress_backtest_pass_ratio": ratio, "stress_scenario_count": int(payload.get("stress_scenario_count", 0) or 0) if payload else 0}, alerts


def check_factor_certification(
    path: str | Path | None,
    scorecard_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    scorecard = _read_json(Path(scorecard_path)) if scorecard_path else {}
    status = str(payload.get("status", "") if payload else "")
    remediation = len(payload.get("required_remediation", []) or []) if payload else 0
    blocker_count = int((payload.get("checks") or {}).get("blocker_count", 0) or 0) if payload else 0
    if not blocker_count and isinstance(scorecard.get("summary"), dict):
        blocker_count = int(scorecard["summary"].get("blocker_count", 0) or 0)
    alerts = []
    if status in {"rejected", "insufficient_data"}:
        alerts.append(MonitoringAlert("error", "factor_certification", "factor certification did not pass", {"status": status}))
    elif status == "conditional":
        alerts.append(MonitoringAlert("warning", "factor_certification", "factor certification is conditional", {"remediation": remediation}))
    return {
        "exists": bool(payload),
        "certification_status": status,
        "certification_passed": bool(payload.get("passed", False)) if payload else False,
        "certification_required_remediation_count": remediation,
        "certification_blocker_count": blocker_count,
    }, alerts


def check_portfolio_lab(
    path: str | Path | None,
    robustness_path: str | Path | None = None,
    trials_path: str | Path | None = None,
    selected_policy_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    robustness = _read_json(Path(robustness_path)) if robustness_path else {}
    selected_policy = _read_json(Path(selected_policy_path)) if selected_policy_path else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    selected_id = summary.get("selected_policy_id") or robustness.get("selected_policy_id") or selected_policy.get("policy_id")
    error_count = int(summary.get("error_count", 0) or 0) if summary else 0
    trial_count = int(summary.get("trial_count", 0) or 0) if summary else 0
    if not trial_count and trials_path and Path(trials_path).exists():
        trial_count = sum(1 for line in Path(trials_path).read_text(encoding="utf-8").splitlines() if line.strip())
    alerts = [MonitoringAlert("warning", "portfolio_lab", "portfolio lab has failed trials", {"error_count": error_count})] if error_count else []
    return {
        "exists": bool(payload),
        "portfolio_lab_status": str(payload.get("status", "missing") if payload else "missing"),
        "portfolio_lab_trial_count": trial_count,
        "selected_portfolio_policy_id": selected_id,
        "selected_portfolio_method": selected_policy.get("portfolio_method") if selected_policy else None,
        "portfolio_lab_error_count": error_count,
        "portfolio_selection_score": float(robustness.get("selected_score", 0.0) or 0.0) if robustness else 0.0,
    }, alerts


def check_portfolio_certification(
    path: str | Path | None,
    scorecard_path: str | Path | None = None,
    certified_policy_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(path)) if path else {}
    scorecard = _read_json(Path(scorecard_path)) if scorecard_path else {}
    certified_policy = _read_json(Path(certified_policy_path)) if certified_policy_path else {}
    status = str(payload.get("status", "") if payload else "")
    remediation = len(payload.get("required_remediation", []) or []) if payload else 0
    blocker_count = int((payload.get("checks") or {}).get("blocker_count", 0) or 0) if payload else 0
    if not blocker_count and isinstance(scorecard.get("summary"), dict):
        blocker_count = int(scorecard["summary"].get("blocker_count", 0) or 0)
    alerts = []
    if status in {"rejected", "insufficient_data"}:
        alerts.append(MonitoringAlert("error", "portfolio_certification", "portfolio certification did not pass", {"status": status}))
    elif status == "conditional":
        alerts.append(MonitoringAlert("warning", "portfolio_certification", "portfolio certification is conditional", {"remediation": remediation}))
    return {
        "exists": bool(payload),
        "portfolio_certification_status": status,
        "portfolio_certification_passed": bool(payload.get("passed", False)) if payload else False,
        "portfolio_certification_required_remediation_count": remediation,
        "portfolio_certification_blocker_count": blocker_count,
        "portfolio_policy_id": (payload.get("portfolio_policy_id") if payload else None) or certified_policy.get("policy_id"),
        "certified_portfolio_policy_exists": bool(certified_policy),
    }, alerts


def check_uncertified_production_candidate(store: LocalFactorStore, certification_decision_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    latest = store.load_latest_factor(status="production_candidate", factor_type="composite") or store.load_latest_factor(status="production_candidate")
    decision = _read_json(Path(certification_decision_path)) if certification_decision_path else {}
    status = str(decision.get("status", "")) if decision else ""
    uncertified = latest is not None and status not in {"certified", "conditional"}
    alerts = [MonitoringAlert("warning", "uncertified_production_candidate", "production candidate has no passing certification")] if uncertified else []
    return {
        "exists": latest is not None,
        "factor_id": latest.factor_id if latest else None,
        "certification_status": status,
        "uncertified_production_candidate": bool(uncertified),
    }, alerts


def check_production_orchestrator(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    status = str(payload.get("status", "") if payload else "")
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    alerts = []
    if status in {"blocked", "failed"}:
        alerts.append(MonitoringAlert("error", "production_orchestrator", "production run is not successful", {"status": status}))
    elif status == "waiting_approval":
        alerts.append(MonitoringAlert("info", "production_orchestrator", "production run is waiting for order approval"))
    return {
        "exists": bool(payload),
        "production_run_id": payload.get("production_run_id") if payload else None,
        "production_run_status": status,
        "production_run_mode": payload.get("run_mode", "") if payload else "",
        "production_phase_failed_count": int(summary.get("phase_failed_count", 0) or 0) if isinstance(summary, dict) else 0,
        "production_phase_blocked_count": int(summary.get("phase_blocked_count", 0) or 0) if isinstance(summary, dict) else 0,
        "close_day_status": str(summary.get("close_day_status", "") or "") if isinstance(summary, dict) else "",
    }, alerts


def check_production_readiness(readiness_path: str | Path | None, gate_results_path: str | Path | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(readiness_path)) if readiness_path else {}
    gates = payload.get("gates", []) if isinstance(payload.get("gates"), list) else []
    if not gates and gate_results_path:
        gates = _read_jsonl(Path(gate_results_path))
    blocker_count = sum(1 for gate in gates if gate.get("status") in {"blocked", "failed"})
    warning_count = sum(1 for gate in gates if gate.get("status") == "warning")
    alerts = []
    if blocker_count:
        alerts.append(MonitoringAlert("error", "production_readiness", "production readiness has blocker gates", {"blocker_count": blocker_count}))
    elif warning_count:
        alerts.append(MonitoringAlert("warning", "production_readiness", "production readiness has warnings", {"warning_count": warning_count}))
    return {
        "exists": bool(payload or gates),
        "production_gate_blocker_count": blocker_count,
        "production_gate_warning_count": warning_count,
        "gate_count": len(gates),
        "status": payload.get("status", "") if payload else "",
    }, alerts


def check_production_phase_failures(phase_runs_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(phase_runs_path)) if phase_runs_path else []
    failed = [row for row in rows if row.get("status") == "failed"]
    blocked = [row for row in rows if row.get("status") == "blocked"]
    waiting = [row for row in rows if row.get("status") == "waiting_approval"]
    alerts = []
    if failed or blocked:
        alerts.append(
            MonitoringAlert(
                "error",
                "production_phase_failures",
                "production phases failed or blocked",
                {"failed": len(failed), "blocked": len(blocked)},
            )
        )
    return {
        "exists": bool(rows),
        "production_phase_count": len(rows),
        "production_phase_failed_count": len(failed),
        "production_phase_blocked_count": len(blocked),
        "production_phase_waiting_count": len(waiting),
    }, alerts


def check_production_gate_blockers(gate_results_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(gate_results_path)) if gate_results_path else []
    blockers = [row for row in rows if row.get("status") in {"blocked", "failed"}]
    warnings = [row for row in rows if row.get("status") == "warning"]
    alerts = []
    if blockers:
        alerts.append(MonitoringAlert("error", "production_gate_blockers", "production gate blockers exist", {"count": len(blockers)}))
    return {
        "exists": bool(rows),
        "production_gate_blocker_count": len(blockers),
        "production_gate_warning_count": len(warnings),
        "gate_count": len(rows),
    }, alerts


def check_shadow_trading_run(shadow_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(shadow_report_path)) if shadow_report_path else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    status = str(payload.get("status", "") if payload else "")
    fill_rate = float(summary.get("fill_rate", 0.0) or summary.get("shadow_fill_rate", 0.0) or 0.0) if isinstance(summary, dict) else 0.0
    alerts = []
    if payload and status not in {"success", "warning"}:
        alerts.append(MonitoringAlert("warning", "shadow_trading_run", "shadow trading run needs review", {"status": status}))
    return {
        "exists": bool(payload),
        "shadow_run_status": status,
        "shadow_order_count": int(summary.get("shadow_order_count", 0) or summary.get("order_count", 0) or summary.get("orders", 0) or 0) if isinstance(summary, dict) else 0,
        "shadow_fill_count": int(summary.get("shadow_fill_count", 0) or summary.get("fill_count", 0) or summary.get("fills", 0) or 0) if isinstance(summary, dict) else 0,
        "shadow_fill_rate": fill_rate,
    }, alerts


def check_shadow_drift(drift_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(drift_report_path)) if drift_report_path else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    drift_rows = payload.get("drift", []) if isinstance(payload.get("drift"), list) else []
    target_drift = float(summary.get("target_weight_drift", 0.0) or summary.get("max_target_weight_drift", 0.0) or 0.0) if isinstance(summary, dict) else 0.0
    position_drift = float(summary.get("position_weight_drift", 0.0) or summary.get("max_position_weight_drift", 0.0) or 0.0) if isinstance(summary, dict) else 0.0
    alerts = []
    if max(abs(target_drift), abs(position_drift)) > 0.05:
        alerts.append(MonitoringAlert("warning", "shadow_drift", "shadow drift is elevated", {"target_weight_drift": target_drift, "position_weight_drift": position_drift}))
    return {
        "exists": bool(payload),
        "shadow_drift_count": len(drift_rows),
        "shadow_target_weight_drift": target_drift,
        "shadow_position_weight_drift": position_drift,
    }, alerts


def check_incidents(incident_report_path: str | Path | None, incident_records_path: str | Path | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(incident_report_path)) if incident_report_path else {}
    incidents = payload.get("incidents", []) if isinstance(payload.get("incidents"), list) else []
    if not incidents and incident_records_path:
        incidents = _read_jsonl(Path(incident_records_path))
    open_items = [item for item in incidents if item.get("status") in {"open", "acknowledged"}]
    critical = [item for item in incidents if item.get("severity") == "critical"]
    alerts = []
    if critical:
        alerts.append(MonitoringAlert("error", "incidents", "critical incidents exist", {"count": len(critical)}))
    elif open_items:
        alerts.append(MonitoringAlert("warning", "incidents", "open incidents exist", {"count": len(open_items)}))
    return {
        "exists": bool(payload or incidents),
        "incident_count": len(incidents),
        "incident_open_count": len(open_items),
        "incident_critical_count": len(critical),
        "incident_unresolved_count": len(open_items),
    }, alerts


def check_unresolved_critical_incidents(incident_report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    result, _alerts = check_incidents(incident_report_path)
    unresolved_critical = int(result.get("incident_critical_count", 0) or 0)
    alerts = [
        MonitoringAlert("error", "unresolved_critical_incidents", "unresolved critical incidents require action", {"count": unresolved_critical})
    ] if unresolved_critical else []
    return {"exists": result.get("exists", False), "incident_unresolved_critical_count": unresolved_critical}, alerts


def check_runbook_completion(runbook_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(runbook_path)) if runbook_path else {}
    steps = payload.get("steps", []) if isinstance(payload.get("steps"), list) else []
    pending = [step for step in steps if step.get("status") in {"pending", "open"}]
    alerts = [MonitoringAlert("warning", "runbook_completion", "runbook has pending steps", {"count": len(pending)})] if pending else []
    return {"exists": bool(payload), "runbook_step_count": len(steps), "runbook_pending_step_count": len(pending)}, alerts


def check_production_close_day_status(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    status = str(summary.get("close_day_status", "") or "")
    alerts = []
    if payload and payload.get("status") not in {"closed", "success", "waiting_approval"} and not status:
        alerts.append(MonitoringAlert("warning", "production_close_day_status", "production run has not reached close day"))
    return {"exists": bool(payload), "close_day_status": status, "production_run_status": payload.get("status", "") if payload else ""}, alerts


def check_production_replay(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    failed = int(summary.get("replay_failed_day_count", 0) or 0)
    blocked = int(summary.get("replay_blocked_day_count", 0) or 0)
    alerts = []
    if failed or blocked:
        alerts.append(MonitoringAlert("error", "production_replay", "production replay has failed or blocked days", {"failed": failed, "blocked": blocked}))
    return {
        "exists": bool(payload),
        "replay_id": payload.get("replay_id") if payload else None,
        "replay_status": payload.get("status", "") if payload else "",
        "replay_day_count": int(summary.get("replay_day_count", 0) or 0),
        "replay_success_day_count": int(summary.get("replay_success_day_count", 0) or 0),
        "replay_failed_day_count": failed,
        "replay_blocked_day_count": blocked,
        "replay_warning_day_count": int(summary.get("replay_warning_day_count", 0) or 0),
    }, alerts


def check_replay_day_failures(days_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    rows = _read_jsonl(Path(days_path)) if days_path else []
    failed = [row for row in rows if row.get("status") == "failed"]
    blocked = [row for row in rows if row.get("status") == "blocked"]
    alerts = []
    if failed or blocked:
        alerts.append(MonitoringAlert("error", "replay_day_failures", "replay day failures exist", {"failed": len(failed), "blocked": len(blocked)}))
    return {"exists": bool(rows), "replay_day_count": len(rows), "replay_failed_day_count": len(failed), "replay_blocked_day_count": len(blocked)}, alerts


def check_shadow_lab(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    perf = payload.get("performance_summary", {}) if isinstance(payload.get("performance_summary"), dict) else {}
    drift = payload.get("drift_summary", {}) if isinstance(payload.get("drift_summary"), dict) else {}
    suggestions = payload.get("calibration_suggestions", []) if isinstance(payload.get("calibration_suggestions"), list) else []
    status = str(payload.get("status", "") if payload else "")
    alerts = []
    if status in {"failed", "error"}:
        alerts.append(MonitoringAlert("error", "shadow_lab", "shadow lab failed", {"status": status}))
    elif suggestions:
        alerts.append(MonitoringAlert("warning", "shadow_lab", "shadow lab produced calibration suggestions", {"count": len(suggestions)}))
    return {
        "exists": bool(payload),
        "shadow_lab_status": status,
        "shadow_day_count": int(perf.get("shadow_day_count", 0) or 0),
        "shadow_cumulative_return": float(perf.get("shadow_cumulative_return", 0.0) or 0.0),
        "shadow_max_drawdown": float(perf.get("shadow_max_drawdown", 0.0) or 0.0),
        "shadow_average_fill_rate": float(perf.get("shadow_average_fill_rate", 0.0) or 0.0),
        "shadow_order_rejection_rate": float(perf.get("shadow_order_rejection_rate", 0.0) or 0.0),
        "shadow_target_weight_drift": float(drift.get("shadow_target_weight_drift", 0.0) or 0.0),
        "shadow_position_weight_drift": float(drift.get("shadow_position_weight_drift", 0.0) or 0.0),
        "calibration_suggestion_count": len(suggestions),
    }, alerts


def check_shadow_drift_aggregate(drift_summary_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(drift_summary_path)) if drift_summary_path else {}
    target = float(payload.get("shadow_target_weight_drift", 0.0) or 0.0) if payload else 0.0
    position = float(payload.get("shadow_position_weight_drift", 0.0) or 0.0) if payload else 0.0
    alerts = []
    if payload and not payload.get("passed", True):
        alerts.append(MonitoringAlert("warning", "shadow_drift_aggregate", "aggregate shadow drift exceeds threshold", {"target": target, "position": position}))
    return {"exists": bool(payload), "shadow_target_weight_drift": target, "shadow_position_weight_drift": position, "shadow_drift_breach_count": int(payload.get("shadow_drift_breach_count", 0) or 0) if payload else 0}, alerts


def check_shadow_calibration_suggestions(suggestions_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(suggestions_path)) if suggestions_path else {}
    suggestions = payload.get("suggestions", []) if isinstance(payload.get("suggestions"), list) else []
    alerts = [MonitoringAlert("warning", "shadow_calibration_suggestions", "shadow calibration suggestions require review", {"count": len(suggestions)})] if suggestions else []
    return {"exists": bool(payload), "calibration_suggestion_count": len(suggestions)}, alerts


def check_live_readiness(decision_path: str | Path | None, scorecard_path: str | Path | None = None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    decision = _read_json(Path(decision_path)) if decision_path else {}
    scorecard = _read_json(Path(scorecard_path)) if scorecard_path else {}
    failed = int((scorecard.get("summary") or {}).get("readiness_failed_check_count", 0) or 0) if isinstance(scorecard.get("summary"), dict) else 0
    remediation = int((scorecard.get("summary") or {}).get("readiness_required_remediation_count", 0) or 0) if isinstance(scorecard.get("summary"), dict) else len(decision.get("required_remediation", []) if isinstance(decision.get("required_remediation"), list) else [])
    status = str(decision.get("status", "") if decision else scorecard.get("status", ""))
    alerts = []
    if status == "not_ready" or failed:
        alerts.append(MonitoringAlert("error", "live_readiness", "live readiness gate is not ready", {"failed": failed}))
    elif status == "conditional":
        alerts.append(MonitoringAlert("warning", "live_readiness", "live readiness is conditional"))
    return {
        "exists": bool(decision or scorecard),
        "live_readiness_status": status,
        "readiness_failed_check_count": failed,
        "readiness_required_remediation_count": remediation,
        "readiness_score": float(decision.get("score", scorecard.get("score", 0.0)) or 0.0) if (decision or scorecard) else 0.0,
    }, alerts


def check_readiness_remediation(decision_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(decision_path)) if decision_path else {}
    remediation = payload.get("required_remediation", []) if isinstance(payload.get("required_remediation"), list) else []
    alerts = [MonitoringAlert("error", "readiness_remediation", "required readiness remediation exists", {"count": len(remediation)})] if remediation else []
    return {"exists": bool(payload), "readiness_required_remediation_count": len(remediation)}, alerts


def check_multi_day_incident_trend(report_path: str | Path | None) -> tuple[dict[str, Any], list[MonitoringAlert]]:
    payload = _read_json(Path(report_path)) if report_path else {}
    days = payload.get("day_results", []) if isinstance(payload.get("day_results"), list) else []
    incident_days = sum(1 for day in days if int(day.get("incident_open_count", 0) or 0) > 0)
    alerts = [MonitoringAlert("warning", "multi_day_incident_trend", "incidents occurred during replay", {"incident_days": incident_days})] if incident_days else []
    return {"exists": bool(payload), "incident_replay_day_count": incident_days}, alerts


def _eod_summary(report_path: str | Path | None) -> dict[str, Any]:
    payload = _read_json(Path(report_path)) if report_path else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    return dict(summary)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
