"""Local production monitoring checks."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from factor_store import LocalFactorStore
from paper_account import LocalPaperAccount, compute_account_performance
from broker_adapter import LocalBrokerStore

from .models import MonitoringAlert


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
