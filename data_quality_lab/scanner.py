"""Streaming scanner for semantic data quality checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from artifact_schema.writer import utc_now

from .checks_core import (
    check_adjustment_factors,
    check_corporate_actions,
    check_daily_bars,
    check_daily_basic,
    check_daily_limits,
    check_financial_features,
    check_index_members,
    check_securities,
    check_trade_calendar,
)
from .checks_cross_dataset import run_cross_dataset_checks
from .checks_event import run_event_checks
from .checks_financial import run_financial_statement_checks
from .checks_index_industry import run_index_industry_checks
from .models import DataQualityFreezeGate, DataQualityIssue, DataQualityLabReport, DataQualityScorecard
from .repair_suggestions import build_repair_suggestions
from .rule_registry import CORE_REQUIRED_DATASETS, OPTIONAL_EVENT_DATASETS, build_rule_registry
from .rules import IssueCollector
from .scorecard import build_freeze_gate, build_scorecard


DEFAULT_DATASETS = [
    "securities",
    "trade_calendar",
    "daily_bars",
    "daily_basic",
    "financial_features",
    "daily_limits",
    "adjustment_factors",
    "index_members",
    "corporate_actions",
    "index_basic",
    "index_daily_bars",
    "index_daily_basic",
    "industry_classification",
    "industry_members",
    "suspensions",
    "name_changes",
    "new_shares",
    "income_statements",
    "balance_sheets",
    "cashflow_statements",
    "earnings_forecasts",
    "earnings_express",
    "disclosure_calendar",
    "financial_audit",
    "main_business",
    "moneyflow",
    "margin_summary",
    "margin_detail",
    "top_list",
    "top_inst",
    "block_trades",
    "holder_number",
    "holder_trades",
    "top10_holders",
    "top10_float_holders",
    "pledge_detail",
    "pledge_stat",
    "repurchases",
    "share_unlocks",
    "hk_holdings",
]


def run_data_quality_scan(
    data_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    datasets: Sequence[str] | None = None,
    raw_data_index_manifest_path: str | Path | None = None,
    raw_landing_report_path: str | Path | None = None,
    profile_name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    expected_trade_days: int | None = None,
    expected_security_count: int | None = None,
    max_sample_issues: int = 100,
    max_records_per_dataset: int | None = None,
    use_raw_data_index: bool = False,
    strict: bool = False,
) -> tuple[DataQualityLabReport, list[DataQualityIssue], list[dict[str, Any]], list[dict[str, Any]]]:
    del expected_trade_days, expected_security_count, raw_landing_report_path
    root = Path(data_dir)
    selected = list(datasets or DEFAULT_DATASETS)
    collector = IssueCollector(max_sample_issues=max_sample_issues)
    records_by_dataset = _load_datasets(root, selected, max_records_per_dataset=max_records_per_dataset, collector=collector)
    _apply_raw_index_summary(raw_data_index_manifest_path, collector, use_raw_data_index=use_raw_data_index)

    securities = records_by_dataset.get("securities", [])
    securities_by_code = {str(record.get("ts_code")): record for record in securities if record.get("ts_code")}
    check_securities(securities, collector)
    open_dates = check_trade_calendar(records_by_dataset.get("trade_calendar", []), collector, start_date=start_date, end_date=end_date)
    daily_bar_keys = check_daily_bars(
        records_by_dataset.get("daily_bars", []),
        collector,
        open_dates=open_dates,
        securities_by_code=securities_by_code,
    )
    daily_basic_keys = check_daily_basic(records_by_dataset.get("daily_basic", []), collector)
    check_adjustment_factors(records_by_dataset.get("adjustment_factors", []), collector)
    daily_limits_by_key = check_daily_limits(records_by_dataset.get("daily_limits", []), collector)
    check_index_members(records_by_dataset.get("index_members", []), collector, securities_by_code)
    check_corporate_actions(records_by_dataset.get("corporate_actions", []), collector)
    check_financial_features(records_by_dataset.get("financial_features", []), collector)
    run_index_industry_checks(records_by_dataset, collector, securities_by_code)
    run_financial_statement_checks(records_by_dataset, collector)
    run_event_checks(records_by_dataset, collector, securities_by_code)
    _check_missing_and_empty(selected, records_by_dataset, collector)
    cross_report = run_cross_dataset_checks(
        records_by_dataset,
        collector,
        open_dates=open_dates,
        daily_bar_keys=daily_bar_keys,
        daily_basic_keys=daily_basic_keys,
        daily_limits_by_key=daily_limits_by_key,
        securities_by_code=securities_by_code,
    )
    issues = collector.issues
    scorecard = build_scorecard(
        records_by_dataset,
        issues,
        metadata={
            "raw_data_index_manifest_path": str(raw_data_index_manifest_path) if raw_data_index_manifest_path else None,
            "raw_data_index_used": bool(use_raw_data_index and raw_data_index_manifest_path),
            "strict": bool(strict),
        },
    )
    freeze_gate = build_freeze_gate(scorecard, issues)
    repair_suggestions = [item.to_dict() for item in build_repair_suggestions(issues)]
    now = utc_now()
    out = Path(output_dir) if output_dir is not None else root
    paths = _artifact_paths(out)
    report = DataQualityLabReport(
        report_id=f"data_quality_{now.replace(':', '').replace('-', '')}",
        status=freeze_gate.status,
        profile_name=profile_name,
        data_dir=str(root),
        start_date=start_date,
        end_date=end_date,
        scorecard=scorecard,
        freeze_gate=freeze_gate,
        cross_dataset_report=cross_report,
        paths=paths,
        summary=_summary(scorecard, freeze_gate, selected, use_raw_data_index),
        created_at=now,
    )
    return report, issues, repair_suggestions, build_rule_payload()


def build_rule_payload() -> list[dict[str, Any]]:
    return [rule.to_dict() for rule in build_rule_registry()]


def plan_data_quality_run(
    *,
    data_dir: str | Path,
    output_dir: str | Path,
    raw_data_index_manifest_path: str | Path | None = None,
    profile_name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    out = Path(output_dir)
    command = (
        "uv run python -m data_quality_lab.run_quality_lab run "
        f"--data-dir {data_dir} --output-dir {out} --profile-name {profile_name or 'research_data'} "
        f"--start-date {start_date or '<start_date>'} --end-date {end_date or '<end_date>'} --use-raw-data-index --pretty"
    )
    if raw_data_index_manifest_path:
        command += f" --raw-data-index-manifest-path {raw_data_index_manifest_path}"
    return {
        "status": "planned",
        "data_dir": str(data_dir),
        "output_dir": str(out),
        "raw_data_index_manifest_path": str(raw_data_index_manifest_path) if raw_data_index_manifest_path else None,
        "commands": [command],
        "note": "plan-only does not scan data_dir or mutate data.",
    }


def _load_datasets(
    data_dir: Path,
    datasets: Sequence[str],
    *,
    max_records_per_dataset: int | None,
    collector: IssueCollector,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for dataset in datasets:
        path = data_dir / dataset / "records.jsonl"
        records: list[dict[str, Any]] = []
        if not path.exists():
            result[dataset] = records
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if max_records_per_dataset is not None and len(records) >= max_records_per_dataset:
                    break
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    collector.add(
                        "raw_index.stale",
                        dataset,
                        "malformed JSONL record",
                        severity="error",
                        key=f"line:{line_no}",
                        sample={"error": str(exc)[:160]},
                        repair_action="rerun_dataset",
                    )
                    continue
                if not isinstance(payload, dict):
                    collector.add(
                        "raw_index.stale",
                        dataset,
                        "JSONL record is not an object",
                        severity="error",
                        key=f"line:{line_no}",
                        sample={"record_type": type(payload).__name__},
                        repair_action="rerun_dataset",
                    )
                    continue
                records.append(payload)
        result[dataset] = records
    return result


def _apply_raw_index_summary(path: str | Path | None, collector: IssueCollector, *, use_raw_data_index: bool) -> None:
    if not path or not use_raw_data_index:
        return
    p = Path(path)
    if not p.exists():
        collector.add("raw_index.stale", "raw_data_index", "requested raw data index manifest is missing", severity="warning", repair_action="block_freeze_until_repaired")
        return
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        collector.add("raw_index.stale", "raw_data_index", "raw data index manifest is malformed", severity="error", repair_action="block_freeze_until_repaired")
        return
    status = str(payload.get("status") or "")
    if status not in {"fresh", "partial"}:
        collector.add("raw_index.stale", "raw_data_index", f"raw data index status is {status or 'unknown'}", severity="warning", sample={"status": status}, repair_action="block_freeze_until_repaired")
    for item in payload.get("datasets", []) if isinstance(payload.get("datasets"), list) else []:
        if not isinstance(item, dict):
            continue
        if int(item.get("parse_error_count", 0) or 0) > 0:
            collector.add(
                "raw_index.stale",
                str(item.get("dataset") or "raw_data_index"),
                "raw data index reports parse errors",
                severity="error",
                sample={"parse_error_count": item.get("parse_error_count")},
                repair_action="rerun_dataset",
            )
        if int(item.get("duplicate_key_count_estimate", 0) or 0) > 0:
            collector.add(
                "raw_index.stale",
                str(item.get("dataset") or "raw_data_index"),
                "raw data index reports duplicate keys",
                severity="warning",
                sample={"duplicate_key_count_estimate": item.get("duplicate_key_count_estimate")},
                repair_action="compact_dedup",
            )


def _check_missing_and_empty(selected: Sequence[str], records_by_dataset: dict[str, list[dict[str, Any]]], collector: IssueCollector) -> None:
    for dataset in selected:
        records = records_by_dataset.get(dataset, [])
        if records:
            continue
        if dataset in CORE_REQUIRED_DATASETS:
            collector.add("raw_index.stale", dataset, "core dataset is empty or missing", severity="error", repair_action="rerun_dataset")
        elif dataset in OPTIONAL_EVENT_DATASETS:
            collector.add("events.valid_event_date", dataset, "optional event dataset is empty", severity="info", repair_action="allow_with_warning")


def _summary(scorecard: DataQualityScorecard, freeze_gate: DataQualityFreezeGate, selected: Sequence[str], raw_data_index_used: bool) -> dict[str, Any]:
    return {
        "selected_dataset_count": len(selected),
        "data_quality_status": scorecard.status,
        "data_quality_blocker_count": scorecard.blocker_count,
        "data_quality_error_count": scorecard.error_count,
        "data_quality_warning_count": scorecard.warning_count,
        "core_quality_blocker_count": freeze_gate.core_blocker_count,
        "expanded_quality_blocker_count": freeze_gate.expanded_blocker_count,
        "data_quality_can_create_freeze": freeze_gate.can_create_freeze,
        "data_quality_can_run_expanded_alpha": freeze_gate.can_run_expanded_alpha,
        "raw_data_index_used": bool(raw_data_index_used),
    }


def _artifact_paths(output_dir: Path) -> dict[str, str]:
    return {
        "data_quality_lab_report_path": str(output_dir / "data_quality_lab_report.json"),
        "data_quality_scorecard_path": str(output_dir / "data_quality_scorecard.json"),
        "data_quality_rules_path": str(output_dir / "data_quality_rules.json"),
        "data_quality_issues_path": str(output_dir / "data_quality_issues.jsonl"),
        "dataset_quality_summary_path": str(output_dir / "dataset_quality_summary.jsonl"),
        "cross_dataset_quality_report_path": str(output_dir / "cross_dataset_quality_report.json"),
        "data_quality_repair_suggestions_path": str(output_dir / "data_quality_repair_suggestions.jsonl"),
        "data_quality_freeze_gate_path": str(output_dir / "data_quality_freeze_gate.json"),
    }
