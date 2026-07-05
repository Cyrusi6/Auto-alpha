"""CLI for semantic data quality checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .report import dumps, stdout_payload, write_data_quality_lab_report
from .scanner import DEFAULT_DATASETS, plan_data_quality_run, run_data_quality_scan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run semantic QA over local A-share raw datasets.")
    sub = parser.add_subparsers(dest="command")
    for name in ["plan", "run", "scorecard", "report", "smoke"]:
        p = sub.add_parser(name)
        p.add_argument("--data-dir", required=name != "smoke")
        p.add_argument("--raw-data-index-manifest-path")
        p.add_argument("--raw-landing-report-path")
        p.add_argument("--research-readiness-report-path")
        p.add_argument("--output-dir", required=True)
        p.add_argument("--datasets")
        p.add_argument("--profile-name")
        p.add_argument("--start-date")
        p.add_argument("--end-date")
        p.add_argument("--expected-trade-days", type=int)
        p.add_argument("--expected-security-count", type=int)
        p.add_argument("--max-sample-issues", type=int, default=100)
        p.add_argument("--max-records-per-dataset", type=int)
        p.add_argument("--use-raw-data-index", action="store_true")
        p.add_argument("--strict", action="store_true")
        p.add_argument("--fail-on-blocker", action="store_true")
        p.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command is None:
        args.command = "run"
    if args.command == "plan":
        payload = plan_data_quality_run(
            data_dir=args.data_dir,
            output_dir=args.output_dir,
            raw_data_index_manifest_path=args.raw_data_index_manifest_path,
            profile_name=args.profile_name,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        (Path(args.output_dir) / "data_quality_plan.json").write_text(dumps(payload, True), encoding="utf-8")
        print(dumps(payload, args.pretty))
        return 0
    if args.command == "smoke":
        data_dir = Path(args.data_dir) if args.data_dir else Path(args.output_dir) / "sample_data"
        if not (data_dir / "securities" / "records.jsonl").exists():
            _write_smoke_data(data_dir)
        args.data_dir = str(data_dir)
        args.datasets = args.datasets or "securities,trade_calendar,daily_bars,daily_basic,daily_limits,adjustment_factors,financial_features,index_members,corporate_actions,hk_holdings"
    datasets = _parse_csv(args.datasets) if args.datasets else DEFAULT_DATASETS
    report, issues, suggestions, rules = run_data_quality_scan(
        args.data_dir,
        output_dir=args.output_dir,
        datasets=datasets,
        raw_data_index_manifest_path=args.raw_data_index_manifest_path,
        raw_landing_report_path=args.raw_landing_report_path,
        profile_name=args.profile_name,
        start_date=args.start_date,
        end_date=args.end_date,
        expected_trade_days=args.expected_trade_days,
        expected_security_count=args.expected_security_count,
        max_sample_issues=args.max_sample_issues,
        max_records_per_dataset=args.max_records_per_dataset,
        use_raw_data_index=args.use_raw_data_index,
        strict=args.strict,
    )
    paths = write_data_quality_lab_report(report, issues, suggestions, rules, args.output_dir)
    payload = stdout_payload(report, paths)
    print(dumps(payload, args.pretty))
    if args.fail_on_blocker and not report.freeze_gate.can_create_freeze:
        return 1
    return 0


def _parse_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _write_smoke_data(data_dir: Path) -> None:
    records = {
        "securities": [
            {"ts_code": "000001.SZ", "name": "Ping An Bank", "list_status": "L", "list_date": "19910403"},
            {"ts_code": "000002.SZ", "name": "Vanke", "list_status": "L", "list_date": "19910129"},
        ],
        "trade_calendar": [
            {"cal_date": "20240102", "trade_date": "20240102", "is_open": True},
            {"cal_date": "20240103", "trade_date": "20240103", "is_open": True},
            {"cal_date": "20240104", "trade_date": "20240104", "is_open": True},
        ],
        "daily_bars": [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "pre_close": 10.0, "pct_chg": 2.0, "volume": 1000, "amount": 10200},
            {"ts_code": "000002.SZ", "trade_date": "20240102", "open": 20.0, "high": 20.5, "low": 19.8, "close": 20.2, "pre_close": 20.0, "pct_chg": 1.0, "volume": 2000, "amount": 40400},
        ],
        "daily_basic": [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "turnover_rate": 1.2, "volume_ratio": 0.8, "total_mv": 100000.0, "circ_mv": 90000.0, "pb": 0.9},
            {"ts_code": "000002.SZ", "trade_date": "20240102", "turnover_rate": 1.1, "volume_ratio": 0.9, "total_mv": 200000.0, "circ_mv": 180000.0, "pb": 1.0},
        ],
        "daily_limits": [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "up_limit": 11.0, "down_limit": 9.0},
            {"ts_code": "000002.SZ", "trade_date": "20240102", "up_limit": 22.0, "down_limit": 18.0},
        ],
        "adjustment_factors": [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "adj_factor": 1.0},
            {"ts_code": "000002.SZ", "trade_date": "20240102", "adj_factor": 1.0},
        ],
        "financial_features": [
            {"ts_code": "000001.SZ", "end_date": "20231231", "ann_date": "20240102", "roe": 0.1},
            {"ts_code": "000002.SZ", "end_date": "20231231", "ann_date": "20240102", "roe": 0.2},
        ],
        "index_members": [
            {"index_code": "000300.SH", "trade_date": "20240102", "ts_code": "000001.SZ", "weight": 50.0},
            {"index_code": "000300.SH", "trade_date": "20240102", "ts_code": "000002.SZ", "weight": 50.0},
        ],
        "corporate_actions": [
            {"ts_code": "000001.SZ", "ann_date": "20240102", "ex_date": "20240104", "cash_div": 0.0},
        ],
        "hk_holdings": [],
    }
    for dataset, rows in records.items():
        path = data_dir / dataset
        path.mkdir(parents=True, exist_ok=True)
        with (path / "records.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
