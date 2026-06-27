"""Local production monitoring checks."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from factor_store import LocalFactorStore
from paper_account import LocalPaperAccount, compute_account_performance

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
        alert = MonitoringAlert("error", "factor_drift", "no production or approved composite factor found")
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
