"""Dataclasses for provider smoke validation artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ProviderReadinessStatus:
    OK = "OK"
    SKIPPED = "SKIPPED"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ProviderDiagnosticCode:
    missing_token = "missing_token"
    network_disabled = "network_disabled"
    permission_denied = "permission_denied"
    rate_limited = "rate_limited"
    invalid_token = "invalid_token"
    invalid_field = "invalid_field"
    missing_fields = "missing_fields"
    empty_response = "empty_response"
    malformed_payload = "malformed_payload"
    network_error = "network_error"
    timeout = "timeout"
    stale_data = "stale_data"
    duplicate_keys = "duplicate_keys"
    schema_mismatch = "schema_mismatch"
    unexpected_exception = "unexpected_exception"


@dataclass(frozen=True)
class ApiProbeResult:
    api_name: str
    dataset: str
    status: str
    diagnostic_code: str | None = None
    message: str = ""
    records: int = 0
    requested_fields: list[str] = field(default_factory=list)
    response_fields: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    token_present: bool = False
    redacted_token_suffix: str | None = None
    token_hash_prefix: str | None = None
    network_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DatasetSmokeResult:
    dataset: str
    status: str
    records: int
    path: str = ""
    diagnostic_code: str | None = None
    message: str = ""
    quality_errors: int = 0
    quality_warnings: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FieldCoverageResult:
    dataset: str
    records: int
    expected_fields: list[str]
    present_fields: list[str]
    missing_fields: list[str]
    null_counts: dict[str, int]
    null_ratios: dict[str, float]
    duplicate_key_count: int
    first_date: str | None
    last_date: str | None
    ts_code_count: int
    field_coverage_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DatasetFreshnessResult:
    dataset: str
    last_date: str | None
    as_of_date: str | None
    stale: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IncrementalRecoveryResult:
    ok: bool
    initial_sync_ok: bool
    resume_sync_ok: bool
    validate_only_ok: bool
    compact_ok: bool
    snapshot_ok: bool
    stats_ok: bool
    successful_job_count: int
    failed_job_count: int
    duplicate_counts_before: dict[str, int]
    duplicate_counts_after: dict[str, int]
    cache_hit_count: int
    audit_total_requests: int
    errors: list[str] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuditSummary:
    path: str
    total_requests: int
    success_requests: int
    failed_requests: int
    cache_hit_count: int
    cache_hit_rate: float
    api_name_distribution: dict[str, int]
    dataset_distribution: dict[str, int]
    duration_p50: float
    duration_p95: float
    duration_max: float
    errors_by_category: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BaselineCompareSummary:
    compared: bool
    status: str
    has_differences: bool = False
    difference_count: int = 0
    max_record_count_diff: int = 0
    max_missing_keys: int = 0
    max_numeric_abs_diff: float = 0.0
    date_range_diff_count: int = 0
    report_paths: dict[str, str] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["diff_count"] = self.difference_count
        payload["report_path"] = self.report_paths.get("cross_source_report_path")
        payload["metrics"] = {
            "max_record_count_diff": self.max_record_count_diff,
            "max_missing_keys": self.max_missing_keys,
            "max_numeric_abs_diff": self.max_numeric_abs_diff,
            "date_range_diff_count": self.date_range_diff_count,
        }
        return payload


@dataclass(frozen=True)
class DataSourceSmokeConfig:
    provider: str
    data_dir: str
    output_dir: str
    start_date: str
    end_date: str | None
    datasets: list[str]
    index_codes: list[str]
    allow_network: bool = False
    fake_tushare_scenario: str | None = None
    max_requests: int = 20
    max_records_per_dataset: int | None = None
    chunk_days: int = 30
    mode: str = "overwrite"
    cache_enabled: bool = False
    audit_enabled: bool = False
    validate: bool = False
    stats: bool = False
    snapshot: bool = False
    compact: bool = False
    run_incremental_recovery: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataSourceSmokeReport:
    provider: str
    status: str
    diagnostics: list[ApiProbeResult]
    datasets: list[DatasetSmokeResult]
    field_coverage: list[FieldCoverageResult]
    audit_summary: AuditSummary | None
    incremental_recovery: IncrementalRecoveryResult | None
    baseline_compare: BaselineCompareSummary | None
    quality_summary: dict[str, Any]
    stats_summary: dict[str, Any]
    config: DataSourceSmokeConfig
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        diagnostics = [item.to_dict() for item in self.diagnostics]
        diagnostic_counts: dict[str, int] = {}
        for item in self.diagnostics:
            key = item.diagnostic_code or item.status
            diagnostic_counts[key] = diagnostic_counts.get(key, 0) + 1
        return {
            "provider": self.provider,
            "status": self.status,
            "diagnostics": diagnostics,
            "provider_probe": diagnostics,
            "diagnostic_counts": diagnostic_counts,
            "datasets": [item.to_dict() for item in self.datasets],
            "field_coverage": [item.to_dict() for item in self.field_coverage],
            "audit_summary": self.audit_summary.to_dict() if self.audit_summary else None,
            "incremental_recovery": self.incremental_recovery.to_dict() if self.incremental_recovery else None,
            "baseline_compare": self.baseline_compare.to_dict() if self.baseline_compare else None,
            "quality_summary": self.quality_summary,
            "stats_summary": self.stats_summary,
            "config": self.config.to_dict(),
            "paths": self.paths,
        }
