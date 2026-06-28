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
