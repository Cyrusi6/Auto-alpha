"""Validation checks for local matrix caches."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .builder import DEFAULT_MATRIX_FIELDS
from .models import MatrixValidationReport
from .reader import MatrixStoreReader


def validate_matrix_cache(cache_dir: str | Path) -> MatrixValidationReport:
    cache_path = Path(cache_dir)
    errors: list[str] = []
    warnings: list[str] = []
    fields: list[str] = []
    n_stocks = 0
    n_dates = 0

    try:
        reader = MatrixStoreReader(cache_path)
        metadata = reader.load_metadata()
        ts_codes = reader.load_ts_codes()
        trade_dates = reader.load_trade_dates()
        fields = reader.load_fields()
        n_stocks = len(ts_codes)
        n_dates = len(trade_dates)
        if int(metadata.get("n_stocks", n_stocks)) != n_stocks:
            errors.append("metadata n_stocks does not match ts_codes")
        if int(metadata.get("n_dates", n_dates)) != n_dates:
            errors.append("metadata n_dates does not match trade_dates")
        required = set(DEFAULT_MATRIX_FIELDS)
        if not metadata.get("point_in_time"):
            required -= {"active_mask", "listing_age_days", "pit_available_mask"}
        missing = sorted(required - set(fields))
        if missing:
            errors.append(f"missing required matrix fields: {', '.join(missing)}")
        if metadata.get("point_in_time") and not metadata.get("active_mask_included"):
            warnings.append("point_in_time metadata is enabled but active_mask_included is false")
        if metadata.get("target_return_mode") == "corporate_action_total_return" and "total_return_close" not in fields:
            errors.append("target_return_mode corporate_action_total_return requires total_return_close field")
        if metadata.get("corporate_action_aware") and "corporate_action_flag" not in fields:
            warnings.append("corporate_action_aware metadata is enabled but corporate_action_flag is missing")
        for field in fields:
            try:
                array = reader.load_field(field)
            except FileNotFoundError as exc:
                errors.append(str(exc))
                continue
            expected_shape = (n_stocks,) if field == "industry_codes" else (n_stocks, n_dates)
            if tuple(array.shape) != expected_shape:
                errors.append(f"{field} shape {list(array.shape)} != {list(expected_shape)}")
            if np.issubdtype(array.dtype, np.number) and not np.isfinite(array).all():
                errors.append(f"{field} contains non-finite values")
    except Exception as exc:
        errors.append(str(exc))

    report = MatrixValidationReport(
        cache_dir=str(cache_path),
        valid=not errors,
        errors=errors,
        warnings=warnings,
        fields=fields,
        n_stocks=n_stocks,
        n_dates=n_dates,
    )
    (cache_path / "matrix_validation_report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report
