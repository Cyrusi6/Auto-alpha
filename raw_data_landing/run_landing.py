"""CLI for read-only raw data landing QA."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data_pipeline.ashare.pipeline import ASHARE_DATASETS

from .report import build_landing_report, dumps, write_landing_artifacts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only landing QA for raw A-share JSONL datasets.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["scan", "coverage", "freeze-readiness", "report", "smoke"]:
        _add_common(sub.add_parser(name))
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--run-dir")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--datasets")
    parser.add_argument("--core-datasets")
    parser.add_argument("--required-expanded-datasets")
    parser.add_argument("--expected-start-date")
    parser.add_argument("--expected-end-date")
    parser.add_argument("--expected-trade-days", type=int)
    parser.add_argument("--expected-security-count", type=int)
    parser.add_argument("--profile-name")
    parser.add_argument("--index-codes", default="000300.SH")
    parser.add_argument("--fail-on-blocker", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    datasets = _split(args.datasets) or _datasets_from_run_dir(args.run_dir) or (["securities", "trade_calendar", "daily_bars"] if args.command == "smoke" else list(ASHARE_DATASETS))
    report = build_landing_report(
        data_dir=args.data_dir,
        datasets=datasets,
        profile_name=args.profile_name,
        expected_trade_days=args.expected_trade_days,
        expected_security_count=args.expected_security_count,
        index_codes=_split(args.index_codes),
        core_datasets=_split(args.core_datasets) or None,
        required_expanded_datasets=_split(args.required_expanded_datasets) or None,
    )
    paths = write_landing_artifacts(report, args.output_dir)
    payload = {"status": report.freeze_readiness.status, "summary": report.summary, "paths": paths}
    if args.command == "scan":
        payload = {"datasets": [item.to_dict() for item in report.datasets], "paths": paths}
    elif args.command == "coverage":
        payload = {"coverage_matrix": [item.to_dict() for item in report.coverage_matrix], "paths": paths}
    elif args.command == "freeze-readiness":
        payload = {"freeze_readiness": report.freeze_readiness.to_dict(), "paths": paths}
    print(dumps(payload, args.pretty))
    return 1 if args.fail_on_blocker and report.freeze_readiness.blocker_count else 0


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _datasets_from_run_dir(run_dir: str | None) -> list[str]:
    if not run_dir:
        return []
    path = Path(run_dir) / "backfill_plan.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    datasets = ((payload.get("scope") or {}).get("datasets") or []) if isinstance(payload, dict) else []
    return [str(item) for item in datasets if str(item)]


if __name__ == "__main__":
    raise SystemExit(main())
