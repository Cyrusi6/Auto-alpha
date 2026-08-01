"""Freeze readiness gate for raw landing data."""

from __future__ import annotations

import hashlib
import json
from typing import Sequence

from data_pipeline.ashare.dataset_registry import CORE_DATASETS

from .models import RawDatasetCoverageRow, RawDatasetLandingCheck, RawFreezeReadinessDecision

DAILY_COVERAGE_REQUIRED_DATASETS = {"daily_bars", "daily_basic", "daily_limits", "adjustment_factors"}


def evaluate_freeze_readiness(
    checks: Sequence[RawDatasetLandingCheck],
    coverage: Sequence[RawDatasetCoverageRow],
    core_datasets: Sequence[str] | None = None,
    required_expanded_datasets: Sequence[str] | None = None,
    min_core_coverage: float = 0.95,
) -> RawFreezeReadinessDecision:
    by_dataset = {item.dataset: item for item in checks}
    blockers: list[str] = []
    warnings: list[str] = []
    required_core = list(core_datasets) if core_datasets is not None else [dataset for dataset in CORE_DATASETS if dataset in by_dataset]
    for dataset in required_core:
        check = by_dataset.get(dataset)
        if check is None or not check.exists or check.line_count <= 0:
            blockers.append(f"core dataset missing or empty: {dataset}")
        elif check.parse_error_count or check.duplicate_key_estimate:
            blockers.append(f"core dataset has parse/duplicate issues: {dataset}")
    for row in coverage:
        if row.dataset in DAILY_COVERAGE_REQUIRED_DATASETS and row.dataset in required_core and row.coverage_ratio is not None and row.coverage_ratio < min_core_coverage:
            blockers.append(f"core dataset coverage below {min_core_coverage:.0%}: {row.dataset}")
        elif row.status != "ok":
            warnings.extend(row.warnings)
    for dataset in required_expanded_datasets or []:
        check = by_dataset.get(dataset)
        if check is None or not check.exists or check.line_count <= 0:
            warnings.append(f"expanded dataset missing or empty: {dataset}")
    payload = {"blockers": blockers, "warnings": warnings, "datasets": [item.dataset for item in checks]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    status = "blocked" if blockers else ("warning" if warnings else "ready")
    return RawFreezeReadinessDecision(
        decision_id=f"raw_freeze_{digest[:16]}",
        status=status,
        blocker_count=len(blockers),
        warning_count=len(warnings),
        blockers=blockers,
        warnings=warnings,
        checks=[{"dataset": item.dataset, "status": item.status, "records": item.line_count, "warnings": item.warnings} for item in checks],
    )
