"""Data quality checks for local A-share JSONL datasets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .dataset_registry import DATASET_DEFINITIONS
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
    elif dataset_name == "daily_limits":
        _validate_daily_limits(records, issues)
    elif dataset_name == "adjustment_factors":
        _validate_adjustment_factors(records, issues)
    elif dataset_name == "index_members":
        _validate_index_members(records, issues)
    elif dataset_name == "corporate_actions":
        _validate_corporate_actions(records, issues)
    elif dataset_name in DATASET_DEFINITIONS:
        _validate_generic_dataset(dataset_name, records, issues)

    errors = sum(issue.severity == "error" for issue in issues)
    warnings = sum(issue.severity == "warning" for issue in issues)
    return DatasetQualitySummary(
        dataset=dataset_name,
        records=len(records),
        errors=errors,
        warnings=warnings,
        issues=issues,
    )


def validate_all_datasets(
    storage: LocalAshareStorage,
    datasets: Sequence[str] | None = None,
) -> DataQualityReport:
    selected = list(ASHARE_DATASETS if datasets is None else datasets)
    return DataQualityReport(
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        datasets=[
            validate_dataset(dataset_name, storage.read_dataset(dataset_name))
            for dataset_name in selected
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
        list_status = record.get("list_status")
        if list_status not in {None, "", "L", "D", "P"}:
            issues.append(
                DataQualityIssue(
                    dataset="securities",
                    severity="warning",
                    code="invalid_list_status",
                    message="list_status should be one of L, D, P when present",
                    key=key,
                )
            )
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


def _validate_daily_limits(records: Sequence[dict[str, Any]], issues: list[DataQualityIssue]) -> None:
    for record in records:
        dataset = "daily_limits"
        key = _record_key(record, ("ts_code", "trade_date"))
        _require_ts_code(dataset, str(record.get("ts_code", "")), issues)
        _require_date(dataset, "trade_date", record.get("trade_date"), issues, key)
        up_limit = _to_float(record.get("up_limit"))
        down_limit = _to_float(record.get("down_limit"))
        pre_close = _to_float(record.get("pre_close"))
        for field_name, value in {
            "up_limit": up_limit,
            "down_limit": down_limit,
            "pre_close": pre_close,
        }.items():
            if value is None or value <= 0:
                issues.append(
                    DataQualityIssue(
                        dataset=dataset,
                        severity="error",
                        code="invalid_limit_price",
                        message=f"{field_name} must be positive",
                        key=key,
                    )
                )
        if up_limit is not None and down_limit is not None and up_limit < down_limit:
            issues.append(
                DataQualityIssue(
                    dataset=dataset,
                    severity="error",
                    code="up_limit_less_than_down_limit",
                    message="up_limit must be greater than or equal to down_limit",
                    key=key,
                )
            )


def _validate_adjustment_factors(records: Sequence[dict[str, Any]], issues: list[DataQualityIssue]) -> None:
    for record in records:
        dataset = "adjustment_factors"
        key = _record_key(record, ("ts_code", "trade_date"))
        _require_ts_code(dataset, str(record.get("ts_code", "")), issues)
        _require_date(dataset, "trade_date", record.get("trade_date"), issues, key)
        adj_factor = _to_float(record.get("adj_factor"))
        if adj_factor is None or adj_factor <= 0:
            issues.append(
                DataQualityIssue(
                    dataset=dataset,
                    severity="error",
                    code="invalid_adjustment_factor",
                    message="adj_factor must be positive",
                    key=key,
                )
            )


def _validate_index_members(records: Sequence[dict[str, Any]], issues: list[DataQualityIssue]) -> None:
    for record in records:
        dataset = "index_members"
        key = _record_key(record, ("index_code", "ts_code", "trade_date"))
        index_code = str(record.get("index_code", ""))
        if not index_code:
            issues.append(
                DataQualityIssue(
                    dataset=dataset,
                    severity="error",
                    code="invalid_index_code",
                    message="index_code is required",
                    key=key,
                )
            )
        _require_ts_code(dataset, str(record.get("ts_code", "")), issues)
        _require_date(dataset, "trade_date", record.get("trade_date"), issues, key)
        weight = _to_float(record.get("weight"))
        if weight is None or weight < 0:
            issues.append(
                DataQualityIssue(
                    dataset=dataset,
                    severity="error",
                    code="invalid_index_weight",
                    message="weight must be non-negative",
                    key=key,
                )
            )


def _validate_corporate_actions(records: Sequence[dict[str, Any]], issues: list[DataQualityIssue]) -> None:
    implemented = 0
    for record in records:
        dataset = "corporate_actions"
        key = _record_key(record, ("ts_code", "ann_date", "end_date", "ex_date", "div_proc"))
        _require_ts_code(dataset, str(record.get("ts_code", "")), issues)
        valid_any_date = False
        for field_name in (
            "end_date",
            "ann_date",
            "record_date",
            "ex_date",
            "pay_date",
            "div_listdate",
            "imp_ann_date",
            "base_date",
        ):
            value = record.get(field_name)
            if value not in {None, ""}:
                valid_any_date = _require_date(dataset, field_name, value, issues, key) or valid_any_date
        if record.get("ann_date") in {None, ""} and record.get("ex_date") in {None, ""}:
            issues.append(
                DataQualityIssue(
                    dataset=dataset,
                    severity="error",
                    code="missing_action_date",
                    message="corporate action requires at least ann_date or ex_date",
                    key=key,
                )
            )
        if not valid_any_date:
            issues.append(
                DataQualityIssue(
                    dataset=dataset,
                    severity="warning",
                    code="no_valid_action_date",
                    message="corporate action has no valid lifecycle date",
                    key=key,
                )
            )
        status = str(record.get("div_proc") or record.get("raw_status") or "")
        if "实施" in status:
            implemented += 1
        if record.get("ex_date") in {None, ""}:
            issues.append(
                DataQualityIssue(
                    dataset=dataset,
                    severity="warning",
                    code="missing_ex_date",
                    message="corporate action missing ex_date",
                    key=key,
                )
            )
        if record.get("record_date") in {None, ""}:
            issues.append(
                DataQualityIssue(
                    dataset=dataset,
                    severity="warning",
                    code="missing_record_date",
                    message="corporate action missing record_date",
                    key=key,
                )
            )
        if record.get("pay_date") in {None, ""} and _to_float(record.get("cash_div")) not in {None, 0.0}:
            issues.append(
                DataQualityIssue(
                    dataset=dataset,
                    severity="warning",
                    code="missing_pay_date",
                    message="cash dividend action missing pay_date",
                    key=key,
                )
            )
        for field_name in ("cash_div", "cash_div_tax"):
            value = _to_float(record.get(field_name))
            if value is not None and value < 0:
                issues.append(
                    DataQualityIssue(
                        dataset=dataset,
                        severity="error",
                        code="negative_cash_dividend",
                        message=f"{field_name} must be non-negative",
                        key=key,
                    )
                )
        for field_name in ("stk_div", "stk_bo_rate", "stk_co_rate"):
            value = _to_float(record.get(field_name))
            if value is not None and value < 0:
                issues.append(
                    DataQualityIssue(
                        dataset=dataset,
                        severity="error",
                        code="negative_stock_distribution",
                        message=f"{field_name} must be non-negative",
                        key=key,
                    )
                )
    if records and implemented == 0:
        issues.append(
            DataQualityIssue(
                dataset="corporate_actions",
                severity="warning",
                code="no_implemented_actions",
                message="corporate_actions has no implemented events",
            )
        )


def _validate_generic_dataset(
    dataset_name: str,
    records: Sequence[dict[str, Any]],
    issues: list[DataQualityIssue],
) -> None:
    definition = DATASET_DEFINITIONS[dataset_name]
    for record in records:
        key = _record_key(record, definition.primary_key)
        for field_name in definition.primary_key:
            if record.get(field_name) in {None, ""}:
                issues.append(
                    DataQualityIssue(
                        dataset=dataset_name,
                        severity="error",
                        code="missing_primary_key_field",
                        message=f"{field_name} is required for primary key",
                        key=key,
                    )
                )
        for field_name in {definition.date_field, definition.effective_date_field, definition.availability_date_field}:
            if not field_name:
                continue
            value = record.get(field_name)
            if value not in {None, ""}:
                _require_date(dataset_name, field_name, value, issues, key)
            elif field_name == definition.availability_date_field and definition.pit_safe:
                issues.append(
                    DataQualityIssue(
                        dataset=dataset_name,
                        severity="error",
                        code="missing_availability_date",
                        message=f"{field_name} is required for point-in-time availability",
                        key=key,
                    )
                )
        ts_code = record.get("ts_code")
        if ts_code not in {None, ""}:
            _require_ts_code(dataset_name, str(ts_code), issues)
        index_code = record.get("index_code")
        if "index_code" in definition.primary_key and index_code in {None, ""}:
            issues.append(
                DataQualityIssue(
                    dataset=dataset_name,
                    severity="error",
                    code="missing_index_code",
                    message="index_code is required",
                    key=key,
                )
            )
    if records and definition.weak_pit:
        issues.append(
            DataQualityIssue(
                dataset=dataset_name,
                severity="warning",
                code="weak_pit_contract",
                message="dataset has uncertain publication timing and is marked weak_pit",
            )
        )


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
