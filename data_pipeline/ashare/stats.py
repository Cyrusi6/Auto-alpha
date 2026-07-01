"""Dataset statistics for local A-share JSONL files."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .pipeline import ASHARE_DATASETS
from .storage import DATASET_PRIMARY_KEYS, LocalAshareStorage


@dataclass(frozen=True)
class DatasetStats:
    dataset: str
    records: int
    unique_keys: int
    duplicate_keys: int
    first_trade_date: str | None
    last_trade_date: str | None
    ts_code_count: int
    null_counts: dict[str, int]
    file_size_bytes: int
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def compute_dataset_stats(storage: LocalAshareStorage, dataset_name: str) -> DatasetStats:
    records = storage.read_dataset(dataset_name)
    path = storage.dataset_path(dataset_name)
    key_fields = DATASET_PRIMARY_KEYS.get(dataset_name, ())
    keys = [_record_key(record, key_fields) for record in records if key_fields]
    unique_keys = len(set(keys)) if key_fields else len(records)
    trade_dates = sorted(
        str(value)
        for record in records
        for value in [_record_date_value(record)]
        if value not in {None, ""}
    )
    ts_codes = {
        str(record["ts_code"])
        for record in records
        if record.get("ts_code") not in {None, ""}
    }
    return DatasetStats(
        dataset=dataset_name,
        records=len(records),
        unique_keys=unique_keys,
        duplicate_keys=max(0, len(keys) - unique_keys),
        first_trade_date=trade_dates[0] if trade_dates else None,
        last_trade_date=trade_dates[-1] if trade_dates else None,
        ts_code_count=len(ts_codes),
        null_counts=_null_counts(records),
        file_size_bytes=path.stat().st_size if path.exists() else 0,
        updated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )


def compute_all_dataset_stats(
    storage: LocalAshareStorage,
    datasets: list[str] | None = None,
) -> list[DatasetStats]:
    selected = list(ASHARE_DATASETS if datasets is None else datasets)
    return [compute_dataset_stats(storage, dataset_name) for dataset_name in selected]


def write_dataset_stats(stats: list[DatasetStats], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {"datasets": [dataset.to_dict() for dataset in stats]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return output_path


def _record_key(record: dict[str, Any], fields: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(record.get(field) for field in fields)


def _null_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    fields = sorted({field for record in records for field in record})
    for field in fields:
        counts[field] = sum(record.get(field) in {None, ""} for record in records)
    return counts


def _record_date_value(record: dict[str, Any]) -> Any:
    for field in (
        "trade_date",
        "ex_date",
        "pay_date",
        "ann_date",
        "announce_date",
        "record_date",
        "end_date",
        "report_period",
        "suspend_date",
        "resume_date",
        "ipo_date",
        "issue_date",
        "start_date",
        "float_date",
        "in_date",
        "out_date",
    ):
        value = record.get(field)
        if value not in {None, ""}:
            return value
    return None
