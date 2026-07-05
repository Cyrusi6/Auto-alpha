"""Partition summary builders for raw data indexes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .models import RawPartitionRecord
from .registry import partition_type_for_dataset, primary_date_field


@dataclass
class _PartitionAccumulator:
    dataset: str
    partition_key: str
    partition_type: str
    start_date: str | None = None
    end_date: str | None = None
    ts_code: str | None = None
    index_code: str | None = None
    record_count: int = 0
    offset_start: int | None = None
    offset_end: int = 0
    size_bytes_estimate: int = 0
    metadata: dict[str, Any] | None = None

    def add(self, *, date_value: str | None, ts_code: str | None, index_code: str | None, offset_start: int, offset_end: int) -> None:
        self.record_count += 1
        self.offset_start = offset_start if self.offset_start is None else min(self.offset_start, offset_start)
        self.offset_end = max(self.offset_end, offset_end)
        self.size_bytes_estimate += max(0, offset_end - offset_start)
        if date_value:
            self.start_date = date_value if self.start_date is None or date_value < self.start_date else self.start_date
            self.end_date = date_value if self.end_date is None or date_value > self.end_date else self.end_date
        self.ts_code = self.ts_code or ts_code
        self.index_code = self.index_code or index_code

    def to_record(self) -> RawPartitionRecord:
        return RawPartitionRecord(
            dataset=self.dataset,
            partition_key=self.partition_key,
            partition_type=self.partition_type,
            start_date=self.start_date,
            end_date=self.end_date,
            ts_code=self.ts_code,
            index_code=self.index_code,
            record_count=self.record_count,
            offset_start=int(self.offset_start or 0),
            offset_end=int(self.offset_end),
            size_bytes_estimate=int(self.size_bytes_estimate),
            metadata=dict(self.metadata or {}),
        )


class PartitionBuilder:
    def __init__(self, dataset: str, granularity: str = "monthly", bucket_count: int = 128):
        self.dataset = dataset
        self.granularity = granularity
        self.bucket_count = max(1, int(bucket_count))
        self.partition_type = partition_type_for_dataset(dataset, granularity)
        self._items: dict[str, _PartitionAccumulator] = {}

    def add(self, payload: dict[str, Any], offset_start: int, offset_end: int) -> None:
        key, date_value, ts_code, index_code = self._partition_key(payload)
        item = self._items.get(key)
        if item is None:
            item = _PartitionAccumulator(
                dataset=self.dataset,
                partition_key=key,
                partition_type=self.partition_type,
                metadata={"granularity": self.granularity},
            )
            self._items[key] = item
        item.add(date_value=date_value, ts_code=ts_code, index_code=index_code, offset_start=offset_start, offset_end=offset_end)

    def records(self) -> list[RawPartitionRecord]:
        return [self._items[key].to_record() for key in sorted(self._items)]

    def _partition_key(self, payload: dict[str, Any]) -> tuple[str, str | None, str | None, str | None]:
        date_value = _date_value(payload, primary_date_field(self.dataset))
        ts_code = _string(payload.get("ts_code") or payload.get("con_code"))
        index_value = payload.get("index_code") or payload.get("l1_code")
        if self.dataset.startswith("index_"):
            index_value = index_value or payload.get("ts_code")
        index_code = _string(index_value)
        if self.partition_type in {"trade_date_day", "date_day"}:
            value = date_value or "unknown"
            return f"{self.dataset}:{value}", date_value, ts_code, index_code
        if self.partition_type in {"trade_date_month", "date_month"}:
            month = date_value[:6] if date_value and len(date_value) >= 6 else "unknown"
            return f"{self.dataset}:{month}", date_value, ts_code, index_code
        if self.partition_type == "ts_code_bucket":
            bucket = _bucket(ts_code or "", self.bucket_count)
            return f"{self.dataset}:ts_bucket_{bucket:03d}", date_value, ts_code, index_code
        if self.partition_type == "index_code":
            code = index_code or _string(payload.get("l1_code") or payload.get("index_code")) or "unknown"
            return f"{self.dataset}:{code}", date_value, ts_code, code
        return f"{self.dataset}:all", date_value, ts_code, index_code


def _date_value(payload: dict[str, Any], preferred: str | None) -> str | None:
    if preferred and payload.get(preferred):
        return str(payload.get(preferred))
    for key in ("trade_date", "ann_date", "end_date", "report_period", "list_date", "in_date", "start_date", "ipo_date"):
        if payload.get(key):
            return str(payload.get(key))
    return None


def _string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _bucket(value: str, bucket_count: int) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % bucket_count
