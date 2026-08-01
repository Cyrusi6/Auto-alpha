"""Lightweight helpers for landing quality summaries."""

from __future__ import annotations

from .models import RawDatasetLandingCheck


def summarize_landing_checks(checks: list[RawDatasetLandingCheck]) -> dict:
    return {
        "dataset_count": len(checks),
        "missing_dataset_count": sum(1 for item in checks if not item.exists),
        "empty_dataset_count": sum(1 for item in checks if item.exists and item.line_count == 0),
        "parse_error_count": sum(item.parse_error_count for item in checks),
        "duplicate_key_count": sum(item.duplicate_key_estimate for item in checks),
        "records": sum(item.line_count for item in checks),
        "size_bytes": sum(item.size_bytes for item in checks),
    }
