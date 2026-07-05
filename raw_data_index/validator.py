"""Validation and freshness checks for raw data sidecar indexes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import utc_now

from .models import RawDataIndexStatus, RawDataIndexValidationReport


def validate_raw_data_index(
    manifest_path: str | Path | None,
    *,
    data_dir: str | Path | None = None,
    hash_check: bool = False,
) -> RawDataIndexValidationReport:
    if not manifest_path or not Path(manifest_path).exists():
        return RawDataIndexValidationReport(
            status=RawDataIndexStatus.missing,
            manifest_path=str(manifest_path) if manifest_path else None,
            data_dir=str(data_dir) if data_dir else None,
            dataset_count=0,
            stale_dataset_count=0,
            missing_dataset_count=0,
            parse_error_count=0,
            error_count=1,
            warning_count=0,
            issues=[_issue("error", "missing_manifest", "raw data index manifest is missing", None, str(manifest_path) if manifest_path else None)],
            checked_at=utc_now(),
        )
    target = Path(manifest_path)
    try:
        manifest = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return RawDataIndexValidationReport(
            status=RawDataIndexStatus.failed,
            manifest_path=str(target),
            data_dir=str(data_dir) if data_dir else None,
            dataset_count=0,
            stale_dataset_count=0,
            missing_dataset_count=0,
            parse_error_count=0,
            error_count=1,
            warning_count=0,
            issues=[_issue("error", "malformed_manifest", str(exc), None, str(target))],
            checked_at=utc_now(),
        )
    root = Path(data_dir or manifest.get("data_dir") or "")
    issues: list[dict[str, Any]] = []
    stale = 0
    missing = 0
    parse_errors = 0
    for item in manifest.get("datasets", []) if isinstance(manifest.get("datasets"), list) else []:
        if not isinstance(item, dict):
            continue
        dataset = str(item.get("dataset") or "")
        path = Path(item.get("records_path") or root / dataset / "records.jsonl")
        if not path.exists():
            missing += 1
            issues.append(_issue("error", "records_missing", "records file missing after index build", dataset, str(path)))
            continue
        expected_size = int(item.get("file_size_bytes", 0) or 0)
        expected_mtime = float(item.get("source_mtime", 0.0) or 0.0)
        if path.stat().st_size != expected_size:
            stale += 1
            issues.append(_issue("warning", "file_size_changed", "records file size changed after index build", dataset, str(path), {"expected": expected_size, "actual": path.stat().st_size}))
        elif expected_mtime and abs(path.stat().st_mtime - expected_mtime) > 1e-6:
            stale += 1
            issues.append(_issue("warning", "mtime_changed", "records file mtime changed after index build", dataset, str(path), {"expected": expected_mtime, "actual": path.stat().st_mtime}))
        if hash_check and item.get("records_sha256"):
            actual = _hash_file(path)
            if actual != item.get("records_sha256"):
                stale += 1
                issues.append(_issue("warning", "sha256_changed", "records file hash changed after index build", dataset, str(path)))
        parse_errors += int(item.get("parse_error_count", 0) or 0)
    if parse_errors:
        issues.append(_issue("warning", "indexed_parse_errors", "indexed datasets contain parse errors", None, str(target), {"parse_error_count": parse_errors}))
    error_count = sum(item["severity"] == "error" for item in issues)
    warning_count = sum(item["severity"] == "warning" for item in issues)
    status = RawDataIndexStatus.failed if error_count else RawDataIndexStatus.stale if stale else RawDataIndexStatus.partial if parse_errors else RawDataIndexStatus.fresh
    return RawDataIndexValidationReport(
        status=status,
        manifest_path=str(target),
        data_dir=str(root) if str(root) else None,
        dataset_count=len(manifest.get("datasets", []) or []),
        stale_dataset_count=stale,
        missing_dataset_count=missing,
        parse_error_count=parse_errors,
        error_count=error_count,
        warning_count=warning_count,
        issues=issues,
        checked_at=utc_now(),
        index_hash=manifest.get("index_hash"),
    )


def _hash_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _issue(severity: str, code: str, message: str, dataset: str | None, path: str | None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "dataset": dataset,
        "path": path,
        "metadata": dict(metadata or {}),
    }
