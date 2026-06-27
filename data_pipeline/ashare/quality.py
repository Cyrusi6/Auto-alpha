"""Data quality checks for local A-share JSONL datasets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .pipeline import ASHARE_DATASETS
from .storage import DATASET_PRIMARY_KEYS, LocalAshareStorage
from .validators import is_valid_ts_code, is_valid_yyyymmdd


@dataclass(frozen=True)
class DataQualityIssue:
    dataset: str
    severity: str
    code: str
    message: str
    key: str | None = None


@dataclass(frozen=True)
class DatasetQualitySummary:
    dataset: str
    records: int
    errors: int
    warnings: int
    issues: list[DataQualityIssue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "records": self.records,
            "errors": self.errors,
            "warnings": self.warnings,
            "issues": [asdict(issue) for issue in self.issues],
        }


@dataclass(frozen=True)
class DataQualityReport:
    generated_at: str
    datasets: list[DatasetQualitySummary]

    @property
    def has_errors(self) -> bool:
        return any(dataset.errors > 0 for dataset in self.datasets)

    def to_dict(self) -> dict[str, Any]:
        total_errors = sum(dataset.errors for dataset in self.datasets)
        total_warnings = sum(dataset.warnings for dataset in self.datasets)
        return {
            "generated_at": self.generated_at,
            "has_errors": self.has_errors,
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "datasets": [dataset.to_dict() for dataset in self.datasets],
        }


def validate_dataset(dataset_name: str, records: Sequence[dict[str, Any]]) -> DatasetQualitySummary:
    issues: list[DataQualityIssue] = []
    if not records:
        issues.append(
            DataQualityIssue(
                dataset=dataset_name,
                severity="warning",
                code="empty_dataset",
                message=f"{dataset_name} has no records",
            )
        )

    _check_duplicate_primary_keys(dataset_name, records, issues)

    if dataset_name == "securities":
        _validate_securities(records, issues)
    elif dataset_name == "trade_calendar":
        _validate_trade_calendar(records, issues)
    elif dataset_name == "daily_bars":
        _validate_daily_bars(records, issues)
    elif dataset_name == "daily_basic":
        _validate_daily_basic(records, issues)
    elif dataset_name == "financial_features":
        _validate_financial_features(records, issues)

    errors = sum(issue.severity == "error" for issue in issues)
    warnings = sum(issue.severity == "warning" for issue in issues)
    return DatasetQualitySummary(
        dataset=dataset_name,
        records=len(records),
        errors=errors,
        warnings=warnings,
        issues=issues,
    )


def validate_all_datasets(storage: LocalAshareStorage) -> DataQualityReport:
    return DataQualityReport(
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        datasets=[
            validate_dataset(dataset_name, storage.read_dataset(dataset_name))
            for dataset_name in ASHARE_DATASETS
        ],
    )


def write_quality_report(report: DataQualityReport, path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return output_path


def _check_duplicate_primary_keys(
    dataset_name: str,
    records: Sequence[dict[str, Any]],
    issues: list[DataQualityIssue],
) -> None:
    key_fields = DATASET_PRIMARY_KEYS.get(dataset_name)
    if key_fields is None:
        return

    seen: set[tuple[Any, ...]] = set()
    for record in records:
        key = tuple(record.get(field) for field in key_fields)
        if any(value in {None, ""} for value in key):
            continue
        if key in seen:
            issues.append(
                DataQualityIssue(
                    dataset=dataset_name,
                    severity="error",
                    code="duplicate_primary_key",
                    message=f"duplicate primary key for {dataset_name}",
                    key=_format_key(key),
                )
            )
        seen.add(key)


def _validate_securities(records: Sequence[dict[str, Any]], issues: list[DataQualityIssue]) -> None:
    for record in records:
        key = str(record.get("ts_code", ""))
        _require_ts_code("securities", key, issues)
        _require_date("securities", "list_date", record.get("list_date"), issues, key)
        delist_date = record.get("delist_date")
        if delist_date not in {None, ""}:
            _require_date("securities", "delist_date", delist_date, issues, key)


def _validate_trade_calendar(records: Sequence[dict[str, Any]], issues: list[DataQualityIssue]) -> None:
    dates: list[str] = []
    for record in records:
        trade_date = str(record.get("trade_date", ""))
        if _require_date("trade_calendar", "trade_date", trade_date, issues, trade_date):
            dates.append(trade_date)
        for field_name in ("prev_trade_date", "next_trade_date"):
            value = record.get(field_name)
            if value not in {None, ""}:
                _require_date("trade_calendar", field_name, value, issues, trade_date)
    if dates != sorted(dates):
        issues.append(
            DataQualityIssue(
                dataset="trade_calendar",
                severity="error",
                code="calendar_unsorted",
                message="trade_calendar records must be sorted by trade_date",
            )
        )


def _validate_daily_bars(records: Sequence[dict[str, Any]], issues: list[DataQualityIssue]) -> None:
    for record in records:
        dataset = "daily_bars"
        key = _record_key(record, ("ts_code", "trade_date"))
        _require_ts_code(dataset, str(record.get("ts_code", "")), issues)
        _require_date(dataset, "trade_date", record.get("trade_date"), issues, key)
        for field_name in ("open", "high", "low", "close", "pre_close"):
            value = _to_float(record.get(field_name))
            if value is None or value < 0:
                issues.append(
                    DataQualityIssue(
                        dataset=dataset,
                        severity="error",
                        code="invalid_price",
                        message=f"{field_name} must be non-negative",
                        key=key,
                    )
                )
        for field_name in ("volume", "amount"):
            value = _to_float(record.get(field_name))
            if value is None or value < 0:
                issues.append(
                    DataQualityIssue(
                        dataset=dataset,
                        severity="error",
                        code="invalid_trade_value",
                        message=f"{field_name} must be non-negative",
                        key=key,
                    )
                )
        high = _to_float(record.get("high"))
        low = _to_float(record.get("low"))
        if high is not None and low is not None and high < low:
            issues.append(
                DataQualityIssue(
                    dataset=dataset,
                    severity="error",
                    code="high_less_than_low",
                    message="high must be greater than or equal to low",
                    key=key,
                )
            )


def _validate_daily_basic(records: Sequence[dict[str, Any]], issues: list[DataQualityIssue]) -> None:
    for record in records:
        dataset = "daily_basic"
        key = _record_key(record, ("ts_code", "trade_date"))
        _require_ts_code(dataset, str(record.get("ts_code", "")), issues)
        _require_date(dataset, "trade_date", record.get("trade_date"), issues, key)


def _validate_financial_features(records: Sequence[dict[str, Any]], issues: list[DataQualityIssue]) -> None:
    for record in records:
        dataset = "financial_features"
        key = _record_key(record, ("ts_code", "report_period", "announce_date"))
        _require_ts_code(dataset, str(record.get("ts_code", "")), issues)
        _require_date(dataset, "report_period", record.get("report_period"), issues, key)
        announce_date = record.get("announce_date")
        if announce_date in {None, ""}:
            issues.append(
                DataQualityIssue(
                    dataset=dataset,
                    severity="error",
                    code="missing_announce_date",
                    message="announce_date is required",
                    key=key,
                )
            )
        else:
            _require_date(dataset, "announce_date", announce_date, issues, key)


def _require_ts_code(dataset: str, ts_code: str, issues: list[DataQualityIssue]) -> bool:
    if is_valid_ts_code(ts_code):
        return True
    issues.append(
        DataQualityIssue(
            dataset=dataset,
            severity="error",
            code="invalid_ts_code",
            message=f"invalid ts_code: {ts_code}",
            key=ts_code or None,
        )
    )
    return False


def _require_date(
    dataset: str,
    field_name: str,
    value: Any,
    issues: list[DataQualityIssue],
    key: str | None,
) -> bool:
    text = "" if value is None else str(value)
    if is_valid_yyyymmdd(text):
        return True
    issues.append(
        DataQualityIssue(
            dataset=dataset,
            severity="error",
            code="invalid_date",
            message=f"{field_name} must be YYYYMMDD",
            key=key,
        )
    )
    return False


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record_key(record: dict[str, Any], fields: Sequence[str]) -> str:
    return _format_key(tuple(record.get(field) for field in fields))


def _format_key(key: tuple[Any, ...]) -> str:
    return "|".join("" if value is None else str(value) for value in key)
