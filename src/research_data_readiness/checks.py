"""Read-only research data readiness checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from raw_data_landing.scanner import scan_datasets

from .dataset_policy import ALL_RESEARCH_DATASETS, dataset_policy
from .models import DatasetPitSafety, DatasetReadinessCheck, DatasetResearchTier


def build_dataset_readiness_checks(
    data_dir: str | Path,
    datasets: list[str] | None = None,
    raw_landing_report_path: str | Path | None = None,
    dataset_progress_path: str | Path | None = None,
) -> list[DatasetReadinessCheck]:
    selected = list(datasets or ALL_RESEARCH_DATASETS)
    landing_by_dataset = _landing_checks(raw_landing_report_path)
    if not landing_by_dataset:
        scanned = scan_datasets(data_dir, selected)
        landing_by_dataset = {item.dataset: item.to_dict() for item in scanned}
    progress_by_dataset = _progress_checks(dataset_progress_path)
    checks: list[DatasetReadinessCheck] = []
    for dataset in selected:
        policy = dataset_policy(dataset)
        landing = landing_by_dataset.get(dataset, {})
        progress = progress_by_dataset.get(dataset, {})
        exists = bool(landing.get("exists"))
        record_count = int(landing.get("line_count", landing.get("records", 0)) or 0)
        size_bytes = int(landing.get("size_bytes", 0) or 0)
        parse_errors = int(landing.get("parse_error_count", 0) or 0)
        duplicate_keys = int(landing.get("duplicate_key_estimate", 0) or 0)
        coverage_ratio = _float_or_none(landing.get("coverage_ratio"))
        failed_jobs = int(progress.get("failed_jobs", 0) or 0)
        quarantined_jobs = int(progress.get("quarantined_jobs", 0) or 0)
        pending_jobs = int(progress.get("pending_jobs", 0) or 0)
        blockers: list[str] = []
        warnings: list[str] = []
        if not exists:
            blockers.append("dataset file is missing")
        elif record_count <= 0 and policy.tier in {DatasetResearchTier.core_required, DatasetResearchTier.financial_required}:
            blockers.append("dataset is empty")
        elif record_count <= 0:
            warnings.append("dataset is empty")
        if parse_errors:
            blockers.append(f"dataset has {parse_errors} JSONL parse errors")
        if failed_jobs:
            blockers.append(f"dataset has {failed_jobs} failed backfill jobs")
        if quarantined_jobs:
            blockers.append(f"dataset has {quarantined_jobs} quarantined backfill jobs")
        if pending_jobs and policy.tier == DatasetResearchTier.core_required:
            blockers.append(f"core dataset has {pending_jobs} pending backfill jobs")
        elif pending_jobs:
            warnings.append(f"dataset has {pending_jobs} pending backfill jobs")
        if duplicate_keys:
            warnings.append(f"dataset has estimated duplicate primary keys: {duplicate_keys}")
        if policy.pit_safety == DatasetPitSafety.unsafe_missing_availability:
            if policy.tier in {DatasetResearchTier.core_required, DatasetResearchTier.financial_required}:
                blockers.append("dataset lacks a reliable availability date for PIT use")
            else:
                warnings.append("dataset lacks a reliable availability date for PIT use")
        elif policy.pit_safety == DatasetPitSafety.weak_pit:
            warnings.append("dataset has weak PIT timing and requires review before feature use")
        elif policy.pit_safety == DatasetPitSafety.event_date_only:
            warnings.append("dataset must be shifted according to after-close or next-open availability")
        severity = "error" if blockers else ("warning" if warnings else "info")
        status = "blocked" if blockers else ("warning" if warnings else "ready")
        checks.append(
            DatasetReadinessCheck(
                dataset=dataset,
                tier=policy.tier,
                status=status,
                severity=severity,
                record_count=record_count,
                size_bytes=size_bytes,
                coverage_ratio=coverage_ratio,
                date_coverage={"first_date": landing.get("first_date"), "last_date": landing.get("last_date")},
                ts_code_coverage={"ts_code_count": int(landing.get("ts_code_count", 0) or 0)},
                pit_safety=policy.pit_safety,
                availability_field=policy.availability_field,
                blockers=blockers,
                warnings=warnings,
            )
        )
    return checks


def summarize_checks(
    checks: list[DatasetReadinessCheck],
    observer_report_path: str | Path | None = None,
    freeze_readiness_path: str | Path | None = None,
    repair_plan_path: str | Path | None = None,
    postprocess_plan_path: str | Path | None = None,
    matrix_freshness_report_path: str | Path | None = None,
) -> dict[str, Any]:
    observer = _read_json(observer_report_path)
    freeze = _read_json(freeze_readiness_path)
    repair = _read_json(repair_plan_path)
    postprocess = _read_json(postprocess_plan_path)
    matrix = _read_json(matrix_freshness_report_path)
    total_size = sum(item.size_bytes for item in checks)
    latest_trade_date = max((str(item.date_coverage.get("last_date")) for item in checks if item.date_coverage.get("last_date")), default="")
    return {
        "dataset_count": len(checks),
        "missing_core_dataset_count": sum(1 for item in checks if item.tier == DatasetResearchTier.core_required and "dataset file is missing" in item.blockers),
        "incomplete_core_dataset_count": sum(1 for item in checks if item.tier == DatasetResearchTier.core_required and item.status != "ready"),
        "failed_job_count": int((observer.get("summary") or {}).get("backfill_failed_jobs", 0) or 0),
        "quarantined_job_count": int((observer.get("summary") or {}).get("backfill_quarantined_jobs", 0) or 0),
        "pending_job_count": int((observer.get("summary") or {}).get("pending_jobs", (observer.get("summary") or {}).get("backfill_remaining_jobs", 0)) or 0),
        "backfill_progress_ratio": float((observer.get("summary") or {}).get("backfill_progress_ratio", (observer.get("summary") or {}).get("progress_ratio", 0.0)) or 0.0),
        "empty_but_expected_count": sum(1 for item in checks if item.record_count <= 0 and item.tier != DatasetResearchTier.broker_irrelevant),
        "pit_unsafe_required_dataset_count": sum(
            1
            for item in checks
            if item.pit_safety == DatasetPitSafety.unsafe_missing_availability
            and item.tier in {DatasetResearchTier.core_required, DatasetResearchTier.financial_required}
        ),
        "weak_pit_dataset_count": sum(1 for item in checks if item.pit_safety == DatasetPitSafety.weak_pit),
        "unsafe_pit_dataset_count": sum(1 for item in checks if item.pit_safety == DatasetPitSafety.unsafe_missing_availability),
        "matrix_freshness_status": matrix.get("status", "missing") if matrix else "missing",
        "raw_freeze_readiness_status": freeze.get("status", "missing") if freeze else "missing",
        "freeze_validation_status": freeze.get("freeze_validation_status", freeze.get("status", "missing")) if freeze else "missing",
        "observer_report_exists": bool(observer),
        "freeze_readiness_exists": bool(freeze),
        "matrix_freshness_exists": bool(matrix),
        "repair_failed_jobs": int(repair.get("failed_jobs", 0) or 0) if repair else 0,
        "postprocess_blocker_count": len(postprocess.get("blockers", [])) if postprocess else 0,
        "data_size_gb": total_size / (1024**3),
        "latest_trade_date": latest_trade_date,
        "staleness_days": None,
    }


def _landing_checks(path: str | Path | None) -> dict[str, dict[str, Any]]:
    payload = _read_json(path)
    if not payload:
        return {}
    rows = payload.get("datasets") if isinstance(payload.get("datasets"), list) else []
    return {str(row.get("dataset")): dict(row) for row in rows if isinstance(row, dict) and row.get("dataset")}


def _progress_checks(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if not path or not Path(path).exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("dataset"):
                rows[str(row["dataset"])] = row
    return rows


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path or not Path(path).exists():
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _float_or_none(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
