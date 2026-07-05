"""Matrix freshness validation."""

from __future__ import annotations

import json
from pathlib import Path

from .diff import diff_matrix_source
from .models import MatrixFreshnessReport


def validate_matrix_freshness(
    data_dir: str | Path,
    matrix_cache_dir: str | Path,
    data_version_manifest_path: str | Path | None = None,
    raw_data_index_manifest_path: str | Path | None = None,
) -> MatrixFreshnessReport:
    diff = diff_matrix_source(data_dir, matrix_cache_dir, data_version_manifest_path, raw_data_index_manifest_path)
    metadata_path = Path(matrix_cache_dir) / "metadata.json"
    issues = list(diff.issues)
    n_stocks = 0
    n_dates = 0
    if not metadata_path.exists():
        issues.append({"severity": "error", "code": "missing_metadata", "message": "matrix metadata.json is missing"})
    else:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            n_stocks = int(metadata.get("n_stocks", 0) or 0)
            n_dates = int(metadata.get("n_dates", 0) or 0)
        except Exception as exc:
            issues.append({"severity": "error", "code": "invalid_metadata", "message": str(exc)})
    if n_stocks <= 0:
        issues.append({"severity": "error", "code": "empty_stock_axis", "message": "matrix cache has no stocks"})
    if n_dates <= 0:
        issues.append({"severity": "error", "code": "empty_date_axis", "message": "matrix cache has no dates"})
    has_error = any(issue.get("severity") == "error" for issue in issues)
    status = "stale" if diff.status == "drift" else "fresh"
    if has_error:
        status = "error"
    return MatrixFreshnessReport(
        status=status,
        matrix_cache_dir=str(matrix_cache_dir),
        n_stocks=n_stocks,
        n_dates=n_dates,
        source_hash=diff.source_hash,
        matrix_hash=diff.matrix_hash,
        issues=issues,
        raw_data_index_status=diff.raw_data_index_status,
        raw_data_index_hash=diff.raw_data_index_hash,
    )
