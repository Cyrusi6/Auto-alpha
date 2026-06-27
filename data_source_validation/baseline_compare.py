"""Baseline comparison helpers for data source smoke validation."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from cross_source_checks.comparator import compare_data_dirs
from cross_source_checks.report import write_cross_source_report

from .models import BaselineCompareSummary


def compare_to_baseline(
    data_dir: str | Path,
    baseline_data_dir: str | Path,
    datasets: Iterable[str],
    output_dir: str | Path | None = None,
    max_record_count_diff: int = 0,
    max_missing_keys: int = 0,
    max_numeric_abs_diff: float = 0.0,
    allow_date_range_diff: bool = False,
) -> BaselineCompareSummary:
    report = compare_data_dirs(baseline_data_dir, data_dir, list(datasets))
    paths: dict[str, str] = {}
    if output_dir is not None:
        json_path, md_path = write_cross_source_report(report, Path(output_dir) / "baseline_compare")
        paths = {"cross_source_report_path": str(json_path), "cross_source_report_md_path": str(md_path)}

    diff_count = 0
    max_count_diff = 0
    max_missing = 0
    max_numeric = 0.0
    date_range_diffs = 0
    for item in report.datasets:
        max_count_diff = max(max_count_diff, abs(int(item.record_count_diff)))
        max_missing = max(max_missing, int(item.missing_keys_left), int(item.missing_keys_right))
        max_numeric = max(max_numeric, float(item.numeric_field_max_abs_diff))
        date_range_diffs += 1 if item.date_range_diff else 0
        if (
            abs(item.record_count_diff) > max_record_count_diff
            or item.missing_keys_left > max_missing_keys
            or item.missing_keys_right > max_missing_keys
            or item.numeric_field_max_abs_diff > max_numeric_abs_diff
            or (item.date_range_diff and not allow_date_range_diff)
        ):
            diff_count += 1

    return BaselineCompareSummary(
        compared=bool(report.datasets),
        status="warning" if diff_count else "ok",
        has_differences=report.has_differences,
        difference_count=diff_count,
        max_record_count_diff=max_count_diff,
        max_missing_keys=max_missing,
        max_numeric_abs_diff=max_numeric,
        date_range_diff_count=date_range_diffs,
        report_paths=paths,
    )
