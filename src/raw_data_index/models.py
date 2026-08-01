"""Dataclasses for raw data sidecar indexes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class RawDataIndexStatus:
    missing = "missing"
    fresh = "fresh"
    stale = "stale"
    partial = "partial"
    failed = "failed"
    skipped = "skipped"
    blocked = "blocked"
    planned = "planned"


@dataclass(frozen=True)
class RawDatasetIndex:
    dataset: str
    records_path: str
    records_sha256: str
    file_size_bytes: int
    record_count: int
    parse_error_count: int
    first_date: str | None
    last_date: str | None
    ts_code_count: int
    index_code_count: int
    ann_date_first: str | None
    ann_date_last: str | None
    end_date_first: str | None
    end_date_last: str | None
    primary_key_fields: list[str]
    duplicate_key_count_estimate: int
    null_field_summary: dict[str, int]
    partition_count: int
    built_at: str
    source_mtime: float
    status: str = RawDataIndexStatus.fresh
    warning_count: int = 0
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawPartitionRecord:
    dataset: str
    partition_key: str
    partition_type: str
    start_date: str | None
    end_date: str | None
    ts_code: str | None
    index_code: str | None
    record_count: int
    offset_start: int
    offset_end: int
    size_bytes_estimate: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawDataIndexManifest:
    index_id: str
    status: str
    data_dir: str
    profile_name: str | None
    start_date: str | None
    end_date: str | None
    partition_granularity: str
    dataset_count: int
    total_records: int
    total_size_bytes: int
    total_parse_errors: int
    total_duplicate_key_estimate: int
    partition_count: int
    index_hash: str
    dataset_indexes_path: str
    partitions_path: str
    issues_path: str
    built_at: str
    datasets: list[dict[str, Any]]
    source_summary: dict[str, Any] = field(default_factory=dict)
    safety: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawDataIndexValidationReport:
    status: str
    manifest_path: str | None
    data_dir: str | None
    dataset_count: int
    stale_dataset_count: int
    missing_dataset_count: int
    parse_error_count: int
    error_count: int
    warning_count: int
    issues: list[dict[str, Any]]
    checked_at: str
    index_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawDataIndexReport:
    report_id: str
    status: str
    data_dir: str
    output_dir: str
    manifest_path: str | None
    validation_report_path: str | None
    dataset_count: int
    total_records: int
    total_size_bytes: int
    total_parse_errors: int
    stale_dataset_count: int
    missing_dataset_count: int
    active_run_blocked: bool
    issues: list[dict[str, Any]]
    summary: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
