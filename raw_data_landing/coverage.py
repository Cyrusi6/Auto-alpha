"""Coverage matrix construction for raw landing datasets."""

from __future__ import annotations

from typing import Sequence

from data_pipeline.ashare.dataset_registry import INDEX_CODE_DATASETS, TRADE_DAY_DATASETS, TS_CODE_SPLIT_DATASETS

from .models import RawDatasetCoverageRow, RawDatasetLandingCheck


def build_coverage_matrix(
    checks: Sequence[RawDatasetLandingCheck],
    expected_trade_days: int | None = None,
    expected_security_count: int | None = None,
    expected_index_codes: int | None = None,
) -> list[RawDatasetCoverageRow]:
    rows: list[RawDatasetCoverageRow] = []
    for check in checks:
        coverage_type = "records"
        expected: int | None = None
        observed = check.line_count
        if check.dataset in TRADE_DAY_DATASETS and expected_trade_days:
            coverage_type = "trade_days"
            expected = int(expected_trade_days)
            observed = _observed_units(check, fallback=check.line_count)
        elif check.dataset in TS_CODE_SPLIT_DATASETS and expected_security_count:
            coverage_type = "securities"
            expected = int(expected_security_count)
            observed = check.ts_code_count
        elif check.dataset in INDEX_CODE_DATASETS and expected_trade_days and expected_index_codes:
            coverage_type = "index_trade_days"
            expected = int(expected_trade_days) * int(expected_index_codes)
            observed = check.line_count
        ratio = None if not expected else min(1.0, float(observed) / float(expected))
        status = "ok" if ratio is None or ratio >= 0.95 else "gap"
        warnings = [] if status == "ok" else [f"coverage below threshold: {ratio:.2%}" if ratio is not None else "coverage unavailable"]
        rows.append(
            RawDatasetCoverageRow(
                dataset=check.dataset,
                coverage_type=coverage_type,
                expected_units=expected,
                observed_units=observed,
                coverage_ratio=ratio,
                status=status,
                warnings=warnings,
            )
        )
    return rows


def _observed_units(check: RawDatasetLandingCheck, fallback: int) -> int:
    if check.first_date and check.last_date and check.line_count:
        return min(fallback, check.line_count)
    return fallback
