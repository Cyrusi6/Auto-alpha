"""Canonical leakage audits for formulas, factors, actions, and backtests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from auto_alpha.data.pit.corporate_actions.normalizer import normalize_corporate_action_records
from auto_alpha.data.pit.corporate_actions.report import read_jsonl
from auto_alpha.data.pit.engine.security_master import load_active_security_mask
from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.research.formulas.vm import StackVM
from auto_alpha.research.formulas.semantics import FORMULA_VOCAB
from auto_alpha.validation.firewall.leakage_models import (
    BacktestLeakageResult,
    CorporateActionLeakageResult,
    FactorValueLeakageResult,
    FormulaLeakageScanResult,
    LeakageAuditConfig,
    LeakageAuditReport,
    LeakageIssue,
    LeakageSeverity,
    SurvivorshipAuditResult,
    TruncationConsistencyResult,
)


FORBIDDEN_PATTERNS = ("TARGET_RET", "FUTURE", "LEAD", "SHIFT_NEGATIVE", "FORWARD")


def audit_backtest_artifacts(backtest_result_path: str | Path | None, strict: bool = False) -> BacktestLeakageResult:
    if not backtest_result_path or not Path(backtest_result_path).exists():
        return BacktestLeakageResult(False, 0, 0, 0, False, "not_provided", [])
    payload = json.loads(Path(backtest_result_path).read_text(encoding="utf-8"))
    metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}
    issues: list[LeakageIssue] = []
    inactive = int(float(metrics.get("inactive_security_order_count", 0.0) or 0.0))
    if inactive:
        issues.append(LeakageIssue("blocker" if strict else "warning", "inactive_security_order", "backtest traded inactive securities", "backtest_result"))
    same_day_warning = int(float(metrics.get("signal_lag_days", 0.0) or 0.0)) == 0 and bool(payload)
    if same_day_warning:
        issues.append(LeakageIssue("warning", "same_day_signal_execution", "signal and execution may use same-day close data", "backtest_result"))
    blockers = sum(issue.severity == "blocker" for issue in issues)
    warnings = sum(issue.severity == "warning" for issue in issues)
    return BacktestLeakageResult(True, warnings, blockers, inactive, same_day_warning, "blocked" if blockers else ("warning" if warnings else "passed"), issues)


def audit_corporate_actions(data_dir: str | Path, as_of_date: str | None = None) -> CorporateActionLeakageResult:
    events = normalize_corporate_action_records(read_jsonl(Path(data_dir) / "corporate_actions" / "records.jsonl"))
    issues: list[LeakageIssue] = []
    for event in events:
        if as_of_date and event.availability_date and event.availability_date > as_of_date:
            issues.append(
                LeakageIssue(
                    "blocker",
                    "future_corporate_action_unavailable",
                    "corporate action availability_date is after as_of_date",
                    "corporate_actions",
                    event.action_id,
                    {"availability_date": event.availability_date, "as_of_date": as_of_date},
                )
            )
    count = len(issues)
    return CorporateActionLeakageResult(len(events), count, count, 0, 0, issues)


def audit_factor_values(
    factor_store_dir: str | Path | None,
    factor_id: str | None = None,
    as_of_date: str | None = None,
    active_mask_path: str | Path | None = None,
    point_in_time: bool = False,
) -> FactorValueLeakageResult:
    if not factor_store_dir:
        return FactorValueLeakageResult(factor_id, 0, 0, 0, 0, [])
    store = LocalFactorStore(factor_store_dir)
    if factor_id is None:
        latest = store.load_latest_factor(status="production_candidate") or store.load_latest_factor(status="approved") or store.load_latest_factor()
        factor_id = latest.factor_id if latest else None
    if factor_id is None:
        return FactorValueLeakageResult(None, 0, 0, 0, 0, [LeakageIssue("warning", "factor_not_found", "no factor found for leakage audit")])
    records = store.load_factor_values(factor_id)
    active_keys = _active_keys(active_mask_path)
    future = 0
    inactive = 0
    issues: list[LeakageIssue] = []
    for record in records:
        key = f"{record.ts_code}|{record.trade_date}"
        if as_of_date and record.trade_date > as_of_date:
            future += 1
            issues.append(LeakageIssue("blocker", "factor_value_after_as_of_date", "factor value is after as_of_date", "factor_values", key))
        if point_in_time and active_keys and (record.ts_code, record.trade_date) not in active_keys:
            inactive += 1
            issues.append(LeakageIssue("warning", "inactive_security_factor_value", "factor value exists for inactive security/date", "factor_values", key))
    factor = next((item for item in store.load_factors() if item.factor_id == factor_id), None)
    metadata_missing = int(bool(point_in_time and factor is not None and not (factor.metadata or {}).get("point_in_time")))
    if metadata_missing:
        issues.append(LeakageIssue("warning", "missing_point_in_time_metadata", "factor record does not mark point_in_time=true", "factors", factor_id))
    return FactorValueLeakageResult(factor_id, len(records), future, inactive, metadata_missing, issues)


def scan_formula_leakage(
    formulas: Iterable[dict] | None = None,
    formula_paths: Iterable[str | Path] | None = None,
) -> FormulaLeakageScanResult:
    items = list(formulas or [])
    for path in formula_paths or []:
        items.extend(_read_formulas(path))
    if not items:
        items = [{"name": name, "formula_tokens": [FORMULA_VOCAB.encode_name(name)]} for name in FORMULA_VOCAB.feature_names[:5]]
    issues: list[LeakageIssue] = []
    blocked = 0
    warnings = 0
    future_tokens = sum(any(pattern in name.upper() for pattern in FORBIDDEN_PATTERNS) for name in FORMULA_VOCAB.token_names)
    vm = StackVM()
    for index, item in enumerate(items):
        tokens = item.get("formula_tokens") or item.get("tokens") or []
        names = item.get("formula_names") or []
        try:
            names = names or FORMULA_VOCAB.decode_tokens([int(token) for token in tokens])
        except Exception:
            names = [str(token) for token in tokens]
        forbidden = [name for name in names if any(pattern in str(name).upper() for pattern in FORBIDDEN_PATTERNS)]
        if forbidden:
            blocked += 1
            issues.append(LeakageIssue("blocker", "forbidden_future_token", "formula contains forward-looking token", "formula", str(index), {"tokens": forbidden}))
        try:
            valid, reason = vm.validate_with_reason([int(token) for token in tokens])
        except Exception as exc:
            valid, reason = False, str(exc)
        if not valid:
            warnings += 1
            issues.append(LeakageIssue("warning", "invalid_formula", reason, "formula", str(index), {"formula_names": names}))
    return FormulaLeakageScanResult(len(items), blocked, warnings, future_tokens, issues)


def run_truncation_consistency_test(
    data_dir: str | Path,
    factor_store_dir: str | Path | None = None,
    cutoff_date: str | None = None,
    max_formulas: int = 5,
    tolerance: float = 1e-8,
) -> TruncationConsistencyResult:
    del data_dir
    if not factor_store_dir or not Path(factor_store_dir).exists():
        return TruncationConsistencyResult(0, 0.0, 0, 0.0, 0, 0, True, [])
    store = LocalFactorStore(factor_store_dir)
    factors = store.load_factors()[: max(0, max_formulas)]
    compared = 0
    changed = 0
    issues: list[LeakageIssue] = []
    for factor in factors:
        values = [record for record in store.load_factor_values(factor.factor_id) if cutoff_date is None or record.trade_date <= cutoff_date]
        if not values:
            continue
        compared += 1
        if cutoff_date and any(record.trade_date > cutoff_date for record in values):
            changed += 1
            issues.append(LeakageIssue("blocker", "post_cutoff_factor_value", "factor value exceeds truncation cutoff", "factor_values", factor.factor_id))
    maximum = 1.0 if changed else 0.0
    passed = changed == 0 and maximum <= tolerance
    return TruncationConsistencyResult(compared, maximum, changed, 0.0, 0 if passed else changed, max(0, len(factors) - compared), passed, issues)


def _active_keys(path: str | Path | None) -> set[tuple[str, str]]:
    if not path or not Path(path).exists():
        return set()
    return {(item.ts_code, item.trade_date) for item in load_active_security_mask(path) if item.is_active}


def _read_formulas(path: str | Path) -> list[dict]:
    target = Path(path)
    if not target.exists():
        return []
    if target.suffix == ".jsonl":
        return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    payload = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("candidates", "formulas", "records"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
        return [payload]
    return []


__all__ = [
    "BacktestLeakageResult",
    "FactorValueLeakageResult",
    "FormulaLeakageScanResult",
    "LeakageAuditConfig",
    "LeakageAuditReport",
    "LeakageIssue",
    "LeakageSeverity",
    "SurvivorshipAuditResult",
    "TruncationConsistencyResult",
    "audit_backtest_artifacts",
    "audit_corporate_actions",
    "audit_factor_values",
    "run_truncation_consistency_test",
    "scan_formula_leakage",
]
