"""CLI entry point for the A-share data pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .ashare import ASHARE_DATASETS, AShareDataConfig, build_pipeline_plan
from .ashare.manager import sync_ashare_datasets


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or inspect the A-share data pipeline.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned A-share datasets without syncing data.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Write local A-share datasets using the selected provider.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    parser.add_argument("--provider", help="Override the configured provider.")
    parser.add_argument("--data-dir", help="Override the configured data directory.")
    parser.add_argument("--start-date", help="Override the configured start date.")
    parser.add_argument("--end-date", help="Override the configured end date.")
    parser.add_argument("--adjust", help="Override the configured adjustment mode.")
    parser.add_argument("--universe", help="Override the configured universe.")
    parser.add_argument(
        "--datasets",
        help="Comma-separated datasets to sync. Defaults to all A-share datasets.",
    )
    parser.add_argument(
        "--mode",
        choices=("overwrite", "append"),
        default="overwrite",
        help="Write mode for synced datasets.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Write a data quality report after sync.",
    )
    parser.add_argument(
        "--quality-report",
        action="store_true",
        help="Write a data quality report after sync.",
    )
    parser.add_argument(
        "--state-file",
        help="Override the pipeline sync state file path.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        config = _config_from_args(args)
        selected_datasets = _parse_datasets(args.datasets)
    except SystemExit as exc:
        return int(exc.code)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    indent = 2 if args.pretty else None
    if not args.sync:
        plan = build_pipeline_plan(config)
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=indent))
        return 0

    try:
        result = sync_ashare_datasets(
            config,
            datasets=selected_datasets,
            mode=args.mode,
            validate=args.validate or args.quality_report,
            state_file=args.state_file,
        )
    except (NotImplementedError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=indent))
    return 0


def _config_from_args(args: argparse.Namespace) -> AShareDataConfig:
    config = AShareDataConfig.from_env()
    overrides = {}

    if args.provider is not None:
        overrides["provider"] = args.provider
    if args.data_dir is not None:
        overrides["data_dir"] = Path(args.data_dir)
    if args.start_date is not None:
        overrides["start_date"] = args.start_date
    if args.end_date is not None:
        overrides["end_date"] = args.end_date or None
    if args.adjust is not None:
        overrides["adjust"] = args.adjust.lower()
    if args.universe is not None:
        overrides["universe"] = args.universe

    return replace(config, **overrides)


def _parse_datasets(value: str | None) -> list[str] | None:
    if value is None:
        return None

    datasets = [item.strip() for item in value.split(",") if item.strip()]
    if not datasets:
        raise ValueError("--datasets must include at least one dataset name")

    unsupported = sorted(set(datasets) - set(ASHARE_DATASETS))
    if unsupported:
        raise ValueError(f"Unsupported A-share datasets: {', '.join(unsupported)}")

    return datasets


if __name__ == "__main__":
    raise SystemExit(main())
