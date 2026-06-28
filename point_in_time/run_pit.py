"""CLI for point-in-time governance reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data_pipeline.ashare.storage import LocalAshareStorage

from .report import write_pit_artifacts
from .security_master import build_security_lifecycle
from .validator import validate_point_in_time_data


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and build point-in-time A-share artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "build-security-master", "build-active-mask", "report"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--data-dir", required=True)
        cmd.add_argument("--output-dir", required=True)
        cmd.add_argument("--as-of-date")
        cmd.add_argument("--start-date")
        cmd.add_argument("--end-date")
        cmd.add_argument("--universe-name")
        cmd.add_argument("--universe-file")
        cmd.add_argument("--min-listing-days", type=int, default=0)
        cmd.add_argument("--exclude-st", action="store_true")
        cmd.add_argument("--include-paused", action="store_true")
        cmd.add_argument("--include-delisted-history", action="store_true", default=True)
        cmd.add_argument("--feature-cutoff-mode", choices=["same_day_after_close", "next_trade_day_open", "previous_trade_day_close"], default="same_day_after_close")
        cmd.add_argument("--strict", action="store_true")
        cmd.add_argument("--fail-on-blocker", action="store_true")
        cmd.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report, survivorship, mask = validate_point_in_time_data(
        data_dir=args.data_dir,
        as_of_date=args.as_of_date,
        start_date=args.start_date,
        end_date=args.end_date,
        min_listing_days=args.min_listing_days,
        exclude_st=args.exclude_st,
        include_paused=args.include_paused,
        include_delisted_history=args.include_delisted_history,
        feature_cutoff_mode=args.feature_cutoff_mode,
    )
    storage = LocalAshareStorage(args.data_dir)
    lifecycle = build_security_lifecycle(storage.read_dataset("securities"))
    paths = write_pit_artifacts(args.output_dir, report, survivorship, lifecycle, mask)
    if args.command == "build-security-master":
        payload = {"records": len(lifecycle), "security_lifecycle_path": paths["security_lifecycle_path"], "paths": paths}
    elif args.command == "build-active-mask":
        payload = {"records": len(mask), "active_security_mask_path": paths["active_security_mask_path"], "paths": paths}
    else:
        payload = report.to_dict() | {"survivorship": survivorship.to_dict(), "paths": paths}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    if args.fail_on_blocker and report.blocker_count:
        return 3
    if args.strict and (report.blocker_count or report.error_count):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
