"""CLI for read-only backfill observation."""

from __future__ import annotations

import argparse

from data_pipeline.ashare.pipeline import ASHARE_DATASETS

from .report import build_observer_report, dumps, stdout_payload, write_observer_artifacts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only observer for running A-share backfills.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["observe", "progress", "repair-plan", "postprocess-plan", "report", "smoke"]:
        _add_common(sub.add_parser(name))
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--staging-dir")
    parser.add_argument("--cache-dir")
    parser.add_argument("--logs-dir")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--profile-name")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--datasets")
    parser.add_argument("--core-datasets")
    parser.add_argument("--expanded-datasets")
    parser.add_argument("--index-codes", default="000300.SH")
    parser.add_argument("--security-list-statuses", default="L")
    parser.add_argument("--rate-limit-per-minute", type=int, default=150)
    parser.add_argument("--expected-trade-days", type=int)
    parser.add_argument("--expected-security-count", type=int)
    parser.add_argument("--env-file-name", default=".env.local")
    parser.add_argument("--real-data-root")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    datasets = _split(args.datasets) or None
    if args.command == "smoke" and not datasets:
        datasets = ["securities", "trade_calendar", "daily_bars"]
    report = build_observer_report(
        run_dir=args.run_dir,
        data_dir=args.data_dir,
        staging_dir=args.staging_dir,
        cache_dir=args.cache_dir,
        logs_dir=args.logs_dir,
        output_dir=args.output_dir,
        profile_name=args.profile_name,
        start_date=args.start_date,
        end_date=args.end_date,
        datasets=datasets,
        index_codes=_split(args.index_codes),
        rate_limit_per_minute=args.rate_limit_per_minute,
        expected_trade_days=args.expected_trade_days,
        expected_security_count=args.expected_security_count,
        env_file_name=args.env_file_name,
    )
    paths = write_observer_artifacts(report, args.output_dir)
    payload = stdout_payload(report, paths)
    if args.command == "progress":
        payload = {"datasets": [item.to_dict() for item in report.datasets], "paths": paths}
    elif args.command == "repair-plan":
        payload = {"repair_plan": report.repair_plan.to_dict(), "paths": paths}
    elif args.command == "postprocess-plan":
        payload = {"postprocess_plan": report.postprocess_plan.to_dict(), "paths": paths}
    print(dumps(payload, args.pretty))
    return 0


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
