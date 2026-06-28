"""Dataclasses for local data lake versioning and freeze governance."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DatasetFingerprint:
    dataset: str
    path: str
    records: int
    size_bytes: int
    sha256: str
    schema_version: str
    primary_key_fields: list[str]
    primary_key_count: int
    duplicate_key_count: int
    first_date: str | None
    last_date: str | None
    ts_code_count: int
    null_counts: dict[str, int]
    field_hash: str
    updated_at: str
    missing: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DatasetVersionRecord:
    dataset_version_id: str
    provider: str
    data_dir: str
    start_date: str
    end_date: str | None
    datasets: list[str]
    dataset_fingerprints: list[dict[str, Any]]
    quality_report_path: str | None = None
    dataset_stats_path: str | None = None
    api_audit_path: str | None = None
    backfill_run_report_path: str | None = None
    backfill_coverage_report_path: str | None = None
    pit_validation_report_path: str | None = None
    leakage_audit_report_path: str | None = None
    corporate_actions_report_path: str | None = None
    data_source_smoke_report_path: str | None = None
    created_at: str = ""
    created_by: str = "local"
    status: str = "validated"
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataLakeRegistry:
    created_at: str
    dataset_versions: int
    research_freezes: int
    latest_dataset_version_id: str | None
    latest_freeze_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchDataFreeze:
    freeze_id: str
    dataset_version_id: str
    freeze_name: str
    freeze_dir: str
    freeze_mode: str
    data_dir: str
    matrix_cache_dir: str | None
    artifact_paths: dict[str, str | None]
    frozen_at: str
    content_hash: str
    immutable_check_status: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FreezeValidationIssue:
    severity: str
    code: str
    message: str
    path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FreezeValidationReport:
    freeze_id: str | None
    freeze_dir: str
    status: str
    checked_files: int
    error_count: int
    warning_count: int
    issues: list[FreezeValidationIssue]
    content_hash: str | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataLineageGraph:
    created_at: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataLakeReport:
    created_at: str
    registry_dir: str
    versions: list[dict[str, Any]]
    freezes: list[dict[str, Any]]
    latest_dataset_version_id: str | None
    latest_freeze_id: str | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
