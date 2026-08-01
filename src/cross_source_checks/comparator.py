"""Compare governed JSONL datasets across two local data directories."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from data_pipeline.ashare.storage import DATASET_PRIMARY_KEYS

from .models import CrossSourceDatasetDiff, CrossSourceReport


def compare_data_dirs(
    left_data_dir: str | Path,
    right_data_dir: str | Path,
    datasets: list[str],
) -> CrossSourceReport:
    left = Path(left_data_dir)
    right = Path(right_data_dir)
    diffs = [_compare_dataset(dataset, _read_dataset(left, dataset), _read_dataset(right, dataset)) for dataset in datasets]
    return CrossSourceReport(
        left_data_dir=str(left),
        right_data_dir=str(right),
        datasets=diffs,
        has_differences=any(_has_diff(diff) for diff in diffs),
    )


def _compare_dataset(dataset: str, left_records: list[dict[str, Any]], right_records: list[dict[str, Any]]) -> CrossSourceDatasetDiff:
    key_fields = DATASET_PRIMARY_KEYS.get(dataset, ("ts_code", "trade_date"))
    left_by_key = {_record_key(record, key_fields): record for record in left_records}
    right_by_key = {_record_key(record, key_fields): record for record in right_records}
    left_keys = set(left_by_key)
    right_keys = set(right_by_key)
    common_keys = sorted(left_keys & right_keys)
    numeric_diffs: list[float] = []
    for key in common_keys:
        left_record = left_by_key[key]
        right_record = right_by_key[key]
        for field in sorted(set(left_record) & set(right_record)):
            left_value = _to_float(left_record.get(field))
            right_value = _to_float(right_record.get(field))
            if left_value is None or right_value is None:
                continue
            numeric_diffs.append(abs(left_value - right_value))

    left_range = _date_range(left_records)
    right_range = _date_range(right_records)
    return CrossSourceDatasetDiff(
        dataset=dataset,
        left_records=len(left_records),
        right_records=len(right_records),
        record_count_diff=len(left_records) - len(right_records),
        missing_keys_left=len(right_keys - left_keys),
        missing_keys_right=len(left_keys - right_keys),
        numeric_field_max_abs_diff=float(max(numeric_diffs) if numeric_diffs else 0.0),
        numeric_field_mean_abs_diff=float(sum(numeric_diffs) / len(numeric_diffs) if numeric_diffs else 0.0),
        date_range_diff=left_range != right_range,
        left_date_range=left_range,
        right_date_range=right_range,
        ts_code_count_diff=len({str(record.get("ts_code")) for record in left_records if record.get("ts_code")})
        - len({str(record.get("ts_code")) for record in right_records if record.get("ts_code")}),
    )


def _read_dataset(data_dir: Path, dataset: str) -> list[dict[str, Any]]:
    path = data_dir / dataset / "records.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _record_key(record: dict[str, Any], fields: tuple[str, ...]) -> str:
    return "|".join(str(record.get(field, "")) for field in fields)


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_range(records: list[dict[str, Any]]) -> list[str]:
    values = [
        str(record[field])
        for record in records
        for field in ("trade_date", "list_date", "announce_date", "report_period")
        if record.get(field)
    ]
    return [min(values), max(values)] if values else []


def _has_diff(diff: CrossSourceDatasetDiff) -> bool:
    return any(
        [
            diff.record_count_diff != 0,
            diff.missing_keys_left != 0,
            diff.missing_keys_right != 0,
            diff.numeric_field_max_abs_diff > 1e-12,
            diff.date_range_diff,
            diff.ts_code_count_diff != 0,
        ]
    )
