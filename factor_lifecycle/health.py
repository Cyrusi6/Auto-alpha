"""Factor health checks for lifecycle review."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import torch

from factor_store import LocalFactorStore
from model_core.data_loader import AShareDataLoader

from .models import FactorHealthCheck, LifecyclePolicy


def evaluate_factor_health(
    loader: AShareDataLoader,
    factor_store: LocalFactorStore,
    factor_id: str,
    as_of_date: str,
    policy: LifecyclePolicy,
    artifact_paths: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[FactorHealthCheck]]:
    records = factor_store.load_factor_values(factor_id)
    values_by_date = {}
    for record in records:
        values_by_date.setdefault(record.trade_date, {})[record.ts_code] = record.value
    latest_date = max(values_by_date) if values_by_date else ""
    staleness = _date_diff_days(latest_date, as_of_date) if latest_date else 999999
    recent_dates = [date for date in loader.trade_dates if date in values_by_date][-5:]
    total_slots = max(len(recent_dates) * len(loader.ts_codes), 1)
    present = 0
    finite = 0
    matrix = torch.zeros((len(loader.ts_codes), len(loader.trade_dates)), dtype=torch.float32)
    stock_index = {code: idx for idx, code in enumerate(loader.ts_codes)}
    date_index = {date: idx for idx, date in enumerate(loader.trade_dates)}
    for record in records:
        si = stock_index.get(record.ts_code)
        di = date_index.get(record.trade_date)
        if si is None or di is None or record.value is None:
            continue
        present += 1 if record.trade_date in recent_dates else 0
        value = float(record.value)
        if math.isfinite(value):
            finite += 1 if record.trade_date in recent_dates else 0
            matrix[si, di] = value
    coverage = finite / total_slots if total_slots else 0.0
    missing_ratio = 1.0 - coverage
    recent_values = []
    for date in recent_dates:
        for code in loader.ts_codes:
            value = values_by_date.get(date, {}).get(code)
            if value is not None and math.isfinite(float(value)):
                recent_values.append(float(value))
    cross_std = _std(recent_values)
    rank_ics = _rank_ic_series(matrix, loader.target_ret.detach().cpu() if loader.target_ret is not None else None, recent_dates, loader.trade_dates)
    turnover_proxy = _turnover_proxy(matrix, recent_dates, loader.trade_dates)
    metrics: dict[str, Any] = {
        "latest_factor_date": latest_date,
        "staleness_days": float(max(staleness, 0)),
        "recent_coverage": float(coverage),
        "missing_value_ratio": float(missing_ratio),
        "cross_sectional_std": float(cross_std),
        "recent_rank_ic": float(rank_ics[-1]) if rank_ics else 0.0,
        "recent_rank_ic_mean": float(sum(rank_ics) / len(rank_ics)) if rank_ics else 0.0,
        "recent_rank_ic_abs_mean": float(sum(abs(item) for item in rank_ics) / len(rank_ics)) if rank_ics else 0.0,
        "turnover_proxy": float(turnover_proxy),
        "factor_value_count": float(len(records)),
        "universe_coverage_count": float(finite),
    }
    checks = [
        _check("staleness", staleness <= policy.max_staleness_days, staleness, policy.max_staleness_days, "error"),
        _check("recent_coverage", coverage >= policy.min_recent_coverage, coverage, policy.min_recent_coverage, "error"),
        _check("missing_value_ratio", missing_ratio <= policy.max_missing_factor_value_ratio, missing_ratio, policy.max_missing_factor_value_ratio, "error"),
        _check("recent_rank_ic", metrics["recent_rank_ic_mean"] >= policy.min_recent_rank_ic, metrics["recent_rank_ic_mean"], policy.min_recent_rank_ic, "warning"),
        _check("cross_sectional_std", cross_std > 1e-12, cross_std, ">0", "warning"),
    ]
    checks.extend(_artifact_checks(artifact_paths or {}, policy))
    return metrics, checks


def _artifact_checks(paths: dict[str, str], policy: LifecyclePolicy) -> list[FactorHealthCheck]:
    checks: list[FactorHealthCheck] = []
    schema = _read_json(paths.get("artifact_validation_report_path") or paths.get("artifact_validation_report"))
    if schema:
        errors = int(schema.get("error_count", 0) or 0)
        checks.append(_check("artifact_schema_errors", errors <= policy.max_schema_error_count, errors, policy.max_schema_error_count, "error"))
    else:
        checks.append(FactorHealthCheck("artifact_schema", "info", True, message="artifact validation report not provided"))
    data_source = _read_json(paths.get("data_source_smoke_report_path") or paths.get("data_source_smoke_report"))
    if data_source:
        status = str(data_source.get("status") or "")
        errors = 0 if status in {"OK", "WARNING"} else 1
        passed = not policy.require_data_source_ok or status == "OK"
        checks.append(FactorHealthCheck("data_source_status", "error" if not passed else "info", passed, status, "OK", "data source smoke status"))
        checks.append(_check("data_source_errors", errors <= policy.max_data_source_error_count, errors, policy.max_data_source_error_count, "error"))
    else:
        checks.append(FactorHealthCheck("data_source_status", "info", True, message="data source smoke report not provided"))
    backtest = _read_json(paths.get("backtest_result_path") or paths.get("backtest_result"))
    if backtest:
        metrics = backtest.get("metrics", {}) if isinstance(backtest.get("metrics"), dict) else {}
        fill_rate = float(metrics.get("fill_rate", 0.0) or 0.0)
        checks.append(_check("execution_fill_rate", fill_rate >= policy.min_execution_fill_rate, fill_rate, policy.min_execution_fill_rate, "warning"))
    elif policy.require_backtest_metrics:
        checks.append(FactorHealthCheck("backtest_metrics", "error", False, message="backtest metrics required but missing"))
    else:
        checks.append(FactorHealthCheck("backtest_metrics", "info", True, message="backtest metrics not provided"))
    pit = _read_json(paths.get("pit_validation_report_path") or paths.get("pit_validation_report"))
    if pit:
        blockers = int(pit.get("blocker_count", 0) or 0)
        passed = blockers <= policy.max_pit_blocker_count and (not policy.require_point_in_time_passed or str(pit.get("status")) in {"passed", "warning"})
        checks.append(_check("point_in_time_blockers", passed, blockers, policy.max_pit_blocker_count, "error"))
    elif policy.require_point_in_time_passed:
        checks.append(FactorHealthCheck("point_in_time_validation", "error", False, message="PIT validation report required but missing"))
    else:
        checks.append(FactorHealthCheck("point_in_time_validation", "info", True, message="PIT validation report not provided"))
    survivorship = _read_json(paths.get("survivorship_report_path") or paths.get("survivorship_report"))
    if survivorship:
        warnings = int(survivorship.get("warning_count", 0) or 0)
        checks.append(_check("survivorship_warnings", warnings <= policy.max_survivorship_warning_count, warnings, policy.max_survivorship_warning_count, "warning"))
    leakage = _read_json(paths.get("leakage_audit_report_path") or paths.get("leakage_audit_report"))
    if leakage:
        blockers = int(leakage.get("blocker_count", 0) or 0)
        passed = blockers <= policy.max_leakage_blocker_count and (not policy.require_leakage_audit_passed or str(leakage.get("leakage_gate_status") or leakage.get("status")) in {"passed", "warning"})
        checks.append(_check("leakage_blockers", passed, blockers, policy.max_leakage_blocker_count, "error"))
    elif policy.require_leakage_audit_passed:
        checks.append(FactorHealthCheck("leakage_audit", "error", False, message="leakage audit report required but missing"))
    else:
        checks.append(FactorHealthCheck("leakage_audit", "info", True, message="leakage audit report not provided"))
    truncation = _read_json(paths.get("truncation_consistency_report_path") or paths.get("truncation_consistency_report"))
    if truncation:
        checks.append(_check("truncation_consistency", bool(truncation.get("passed", True)), truncation.get("max_abs_diff", 0.0), "passed", "error"))
    return checks


def _check(name: str, passed: bool, value: Any, threshold: Any, severity: str) -> FactorHealthCheck:
    return FactorHealthCheck(
        name=name,
        severity="info" if passed else severity,
        passed=bool(passed),
        value=value,
        threshold=threshold,
        message="ok" if passed else f"{name} outside policy",
    )


def _rank_ic_series(factors: torch.Tensor, target: torch.Tensor | None, recent_dates: list[str], trade_dates: list[str]) -> list[float]:
    if target is None:
        return []
    values = []
    for date in recent_dates:
        idx = trade_dates.index(date)
        x = factors[:, idx]
        y = target[:, idx]
        if torch.std(x) <= 1e-12 or torch.std(y) <= 1e-12:
            values.append(0.0)
            continue
        corr = torch.corrcoef(torch.stack([torch.argsort(torch.argsort(x)).float(), torch.argsort(torch.argsort(y)).float()]))[0, 1]
        values.append(float(torch.nan_to_num(corr).item()))
    return values


def _turnover_proxy(factors: torch.Tensor, recent_dates: list[str], trade_dates: list[str]) -> float:
    if len(recent_dates) < 2:
        return 0.0
    vals = []
    for left, right in zip(recent_dates, recent_dates[1:]):
        a = factors[:, trade_dates.index(left)]
        b = factors[:, trade_dates.index(right)]
        if torch.std(a) <= 1e-12 or torch.std(b) <= 1e-12:
            continue
        corr = torch.corrcoef(torch.stack([torch.argsort(torch.argsort(a)).float(), torch.argsort(torch.argsort(b)).float()]))[0, 1]
        vals.append(1.0 - float(torch.nan_to_num(corr).item()))
    return sum(vals) / len(vals) if vals else 0.0


def _std(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((item - mean) ** 2 for item in values) / len(values))


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.exists():
        return {}
    return json.loads(candidate.read_text(encoding="utf-8"))


def _date_diff_days(left: str, right: str) -> int:
    try:
        return (datetime.strptime(right, "%Y%m%d") - datetime.strptime(left, "%Y%m%d")).days
    except ValueError:
        return 999999
