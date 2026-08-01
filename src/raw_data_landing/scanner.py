"""Streaming scanners for governed raw JSONL datasets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from data_pipeline.ashare.dataset_registry import DATASET_DEFINITIONS, DATASET_PRIMARY_KEYS

from .models import RawDatasetLandingCheck, RawDatasetLandingStatus


def scan_datasets(data_dir: str | Path, datasets: Sequence[str]) -> list[RawDatasetLandingCheck]:
    return [scan_dataset(data_dir, dataset) for dataset in datasets]


def checks_from_raw_data_index(index_payload: dict, datasets: Sequence[str] | None = None) -> list[RawDatasetLandingCheck]:
    selected = set(datasets or [])
    checks: list[RawDatasetLandingCheck] = []
    for item in index_payload.get("datasets", []) if isinstance(index_payload.get("datasets"), list) else []:
        if not isinstance(item, dict):
            continue
        dataset = str(item.get("dataset") or "")
        if selected and dataset not in selected:
            continue
        status = RawDatasetLandingStatus.complete
        warnings = list(item.get("warnings") or [])
        if item.get("status") == "missing":
            status = RawDatasetLandingStatus.missing
        elif item.get("status") == "partial":
            status = RawDatasetLandingStatus.warning if item.get("parse_error_count") else RawDatasetLandingStatus.partial
        elif int(item.get("parse_error_count", 0) or 0) or int(item.get("duplicate_key_count_estimate", 0) or 0):
            status = RawDatasetLandingStatus.warning
        checks.append(
            RawDatasetLandingCheck(
                dataset=dataset,
                status=status,
                records_path=str(item.get("records_path") or ""),
                exists=bool(item.get("status") != "missing"),
                size_bytes=int(item.get("file_size_bytes", 0) or 0),
                line_count=int(item.get("record_count", 0) or 0),
                parse_error_count=int(item.get("parse_error_count", 0) or 0),
                first_date=item.get("first_date"),
                last_date=item.get("last_date"),
                ts_code_count=int(item.get("ts_code_count", 0) or 0),
                duplicate_key_estimate=int(item.get("duplicate_key_count_estimate", 0) or 0),
                null_or_empty_field_count=sum(int(value or 0) for value in (item.get("null_field_summary") or {}).values())
                if isinstance(item.get("null_field_summary"), dict)
                else 0,
                warnings=warnings,
            )
        )
    return checks


def scan_dataset(data_dir: str | Path, dataset: str, duplicate_sample_limit: int = 1_000_000) -> RawDatasetLandingCheck:
    path = Path(data_dir) / dataset / "records.jsonl"
    date_field = _date_field(dataset)
    key_fields = DATASET_PRIMARY_KEYS.get(dataset, ())
    if not path.exists():
        return RawDatasetLandingCheck(
            dataset=dataset,
            status=RawDatasetLandingStatus.missing,
            records_path=str(path),
            exists=False,
            size_bytes=0,
            line_count=0,
            parse_error_count=0,
            first_date=None,
            last_date=None,
            ts_code_count=0,
            duplicate_key_estimate=0,
            null_or_empty_field_count=0,
            warnings=["records.jsonl missing"],
        )
    line_count = 0
    parse_errors = 0
    nulls = 0
    first_date: str | None = None
    last_date: str | None = None
    ts_codes: set[str] = set()
    seen_keys: set[tuple[str, ...]] = set()
    duplicate_keys = 0
    key_tracking_limited = False
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            line_count += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue
            if not isinstance(payload, dict):
                parse_errors += 1
                continue
            nulls += sum(1 for value in payload.values() if value is None or value == "")
            if payload.get("ts_code"):
                ts_codes.add(str(payload.get("ts_code")))
            if date_field and payload.get(date_field):
                value = str(payload.get(date_field))
                first_date = value if first_date is None or value < first_date else first_date
                last_date = value if last_date is None or value > last_date else last_date
            if key_fields and not key_tracking_limited:
                key = tuple(str(payload.get(field, "")) for field in key_fields)
                if key in seen_keys:
                    duplicate_keys += 1
                elif len(seen_keys) < duplicate_sample_limit:
                    seen_keys.add(key)
                else:
                    key_tracking_limited = True
    warnings: list[str] = []
    if parse_errors:
        warnings.append(f"parse errors: {parse_errors}")
    if duplicate_keys:
        warnings.append(f"duplicate primary keys: {duplicate_keys}")
    if key_tracking_limited:
        warnings.append("duplicate key tracking reached sample limit")
    status = RawDatasetLandingStatus.complete
    if line_count == 0:
        status = RawDatasetLandingStatus.partial
        warnings.append("empty records file")
    if parse_errors or duplicate_keys:
        status = RawDatasetLandingStatus.warning
    return RawDatasetLandingCheck(
        dataset=dataset,
        status=status,
        records_path=str(path),
        exists=True,
        size_bytes=path.stat().st_size,
        line_count=line_count,
        parse_error_count=parse_errors,
        first_date=first_date,
        last_date=last_date,
        ts_code_count=len(ts_codes),
        duplicate_key_estimate=duplicate_keys,
        null_or_empty_field_count=nulls,
        warnings=warnings,
    )


def _date_field(dataset: str) -> str | None:
    definition = DATASET_DEFINITIONS.get(dataset)
    if definition is not None:
        return definition.date_field
    if dataset == "securities":
        return "list_date"
    if dataset == "trade_calendar":
        return "trade_date"
    if dataset == "financial_features":
        return "report_period"
    return "trade_date"
