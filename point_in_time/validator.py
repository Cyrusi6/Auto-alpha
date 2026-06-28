"""Point-in-time validation for governed local A-share datasets."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from data_pipeline.ashare.storage import LocalAshareStorage
from data_pipeline.ashare.validators import is_valid_ts_code, is_valid_yyyymmdd

from .contracts import PIT_DATASET_CONTRACTS
from .models import PITValidationIssue, PITValidationReport, SurvivorshipBiasReport
from .security_master import build_active_security_mask, build_security_lifecycle


def validate_point_in_time_data(
    data_dir: str | Path,
    as_of_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    min_listing_days: int = 0,
    exclude_st: bool = False,
    include_paused: bool = False,
    include_delisted_history: bool = True,
    feature_cutoff_mode: str = "same_day_after_close",
) -> tuple[PITValidationReport, SurvivorshipBiasReport, list]:
    storage = LocalAshareStorage(data_dir)
    issues: list[PITValidationIssue] = []
    summaries: dict[str, dict[str, Any]] = {}
    for name, contract in PIT_DATASET_CONTRACTS.items():
        if name in {"universe", "factor_values", "backtest_inputs"}:
            continue
        records = storage.read_dataset(name)
        missing = sorted(field for field in contract.required_fields if records and all(field not in record for record in records))
        for field in missing:
            severity = "warning" if name == "securities" and field == "delist_date" else "error"
            issues.append(PITValidationIssue(severity, "missing_required_field", f"{name} is missing field {field}", name, field))
        summaries[name] = {
            "records": len(records),
            "timing": contract.timing,
            "point_in_time_safe_by_default": contract.point_in_time_safe_by_default,
            "missing_required_fields": missing,
            "weak_contract": not contract.point_in_time_safe_by_default,
        }
    securities = storage.read_dataset("securities")
    lifecycle = build_security_lifecycle(securities)
    status_distribution = _status_distribution(lifecycle)
    if securities and set(status_distribution) <= {"L", "UNKNOWN", "unknown"}:
        issues.append(
            PITValidationIssue(
                "warning",
                "current_only_security_master",
                "securities contains only currently listed status; delisted/paused history may be missing",
                "securities",
            )
        )
    for record in lifecycle:
        if not is_valid_ts_code(record.ts_code):
            issues.append(PITValidationIssue("error", "invalid_ts_code", f"invalid ts_code {record.ts_code}", "securities", record.ts_code))
        if not is_valid_yyyymmdd(record.list_date):
            issues.append(PITValidationIssue("error", "invalid_list_date", "list_date must be YYYYMMDD", "securities", record.ts_code))
        if record.delist_date and not is_valid_yyyymmdd(record.delist_date):
            issues.append(PITValidationIssue("error", "invalid_delist_date", "delist_date must be YYYYMMDD", "securities", record.ts_code))
    financial = storage.read_dataset("financial_features")
    for item in financial:
        key = "|".join(str(item.get(field) or "") for field in ("ts_code", "report_period", "announce_date"))
        announce_date = str(item.get("announce_date") or "")
        report_period = str(item.get("report_period") or "")
        if not announce_date:
            issues.append(PITValidationIssue("blocker", "missing_announce_date", "financial_features requires announce_date", "financial_features", key))
        elif not is_valid_yyyymmdd(announce_date):
            issues.append(PITValidationIssue("error", "invalid_announce_date", "announce_date must be YYYYMMDD", "financial_features", key))
        if announce_date and report_period and announce_date < report_period:
            issues.append(
                PITValidationIssue(
                    "warning",
                    "announce_before_report_period",
                    "announce_date is before report_period; verify source mapping",
                    "financial_features",
                    key,
                )
            )
    corporate_actions = storage.read_dataset("corporate_actions")
    corporate_summary = {
        "records": len(corporate_actions),
        "future_event_count": 0,
        "missing_availability_date_count": 0,
        "invalid_date_order_count": 0,
        "non_implemented_count": 0,
    }
    for item in corporate_actions:
        key = "|".join(str(item.get(field) or "") for field in ("ts_code", "ann_date", "end_date", "ex_date", "div_proc"))
        ann_date = str(item.get("ann_date") or "")
        imp_ann_date = str(item.get("imp_ann_date") or "")
        availability_date = imp_ann_date or ann_date
        ex_date = str(item.get("ex_date") or "")
        pay_date = str(item.get("pay_date") or "")
        record_date = str(item.get("record_date") or "")
        if not availability_date:
            corporate_summary["missing_availability_date_count"] += 1
            issues.append(PITValidationIssue("warning", "missing_corporate_action_availability_date", "corporate action missing ann_date/imp_ann_date", "corporate_actions", key))
        elif not is_valid_yyyymmdd(availability_date):
            issues.append(PITValidationIssue("error", "invalid_corporate_action_availability_date", "ann_date/imp_ann_date must be YYYYMMDD", "corporate_actions", key))
        if as_of_date and availability_date and availability_date > as_of_date:
            corporate_summary["future_event_count"] += 1
            issues.append(PITValidationIssue("blocker", "future_corporate_action_unavailable", "corporate action availability_date is after as_of_date", "corporate_actions", key))
        for field_name, value in {"ex_date": ex_date, "pay_date": pay_date, "record_date": record_date}.items():
            if value and not is_valid_yyyymmdd(value):
                issues.append(PITValidationIssue("error", f"invalid_{field_name}", f"{field_name} must be YYYYMMDD", "corporate_actions", key))
        if availability_date and ex_date and availability_date > ex_date:
            corporate_summary["invalid_date_order_count"] += 1
            issues.append(PITValidationIssue("warning", "availability_after_ex_date", "corporate action availability_date is after ex_date", "corporate_actions", key))
        if pay_date and ex_date and pay_date < ex_date:
            corporate_summary["invalid_date_order_count"] += 1
            issues.append(PITValidationIssue("error", "pay_date_before_ex_date", "corporate action pay_date is before ex_date", "corporate_actions", key))
        if record_date and ex_date and record_date > ex_date:
            corporate_summary["invalid_date_order_count"] += 1
            issues.append(PITValidationIssue("warning", "record_date_after_ex_date", "corporate action record_date is after ex_date", "corporate_actions", key))
        status = str(item.get("div_proc") or item.get("raw_status") or "")
        if "实施" not in status:
            corporate_summary["non_implemented_count"] += 1
    if "corporate_actions" in summaries:
        summaries["corporate_actions"]["corporate_action_summary"] = corporate_summary
    trade_dates = _trade_dates(storage, start_date, end_date)
    mask = build_active_security_mask(
        lifecycle,
        trade_dates,
        min_listing_days=min_listing_days,
        exclude_st=exclude_st,
        include_paused=include_paused,
        include_delisted_history=include_delisted_history,
    )
    coverage = sum(1 for item in mask if item.is_active) / len(mask) if mask else 0.0
    report = PITValidationReport(
        generated_at=_utc_now(),
        data_dir=str(data_dir),
        as_of_date=as_of_date,
        feature_cutoff_mode=feature_cutoff_mode,
        issues=issues,
        dataset_summaries=summaries,
        security_status_distribution=status_distribution,
        active_universe_coverage=coverage,
    )
    survivorship = SurvivorshipBiasReport(
        generated_at=report.generated_at,
        data_dir=str(data_dir),
        current_only_security_master=set(status_distribution) <= {"L", "UNKNOWN", "unknown"},
        securities_total=len(lifecycle),
        listed_count=status_distribution.get("L", 0),
        delisted_count=status_distribution.get("D", 0),
        paused_count=status_distribution.get("P", 0),
        warning_count=sum(1 for issue in issues if issue.code == "current_only_security_master"),
        warnings=[issue.message for issue in issues if issue.code == "current_only_security_master"],
    )
    return report, survivorship, mask


def _trade_dates(storage: LocalAshareStorage, start_date: str | None, end_date: str | None) -> list[str]:
    dates = [
        str(record.get("trade_date"))
        for record in storage.read_dataset("trade_calendar")
        if record.get("is_open") is True and record.get("trade_date")
    ]
    if start_date:
        dates = [date for date in dates if date >= start_date]
    if end_date:
        dates = [date for date in dates if date <= end_date]
    return sorted(dates)


def _status_distribution(lifecycle: list) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in lifecycle:
        key = (item.list_status or "unknown").upper()
        result[key] = result.get(key, 0) + 1
    return result


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
