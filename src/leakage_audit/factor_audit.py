"""Leakage checks for persisted factor values."""

from __future__ import annotations

import json
from pathlib import Path

from factor_store import LocalFactorStore
from point_in_time.security_master import load_active_security_mask

from .models import FactorValueLeakageResult, LeakageIssue


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
    metadata_missing = 0
    if point_in_time and factor is not None and not (factor.metadata or {}).get("point_in_time"):
        metadata_missing = 1
        issues.append(LeakageIssue("warning", "missing_point_in_time_metadata", "factor record does not mark point_in_time=true", "factors", factor_id))
    return FactorValueLeakageResult(factor_id, len(records), future, inactive, metadata_missing, issues)


def _active_keys(path: str | Path | None) -> set[tuple[str, str]]:
    if not path or not Path(path).exists():
        return set()
    return {(item.ts_code, item.trade_date) for item in load_active_security_mask(path) if item.is_active}
