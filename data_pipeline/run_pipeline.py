"""CLI entry point for the A-share data pipeline."""

from __future__ import annotations

import argparse
import json
import sys

from .ashare import AShareDataConfig, build_pipeline_plan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print the A-share data pipeline plan.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned A-share datasets without syncing data.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Reserved for future real data synchronization.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.sync:
        print(
            "A-share data synchronization is not implemented yet; "
            "run without --sync to inspect the dry-run plan.",
            file=sys.stderr,
        )
        return 2

    config = AShareDataConfig.from_env()
    plan = build_pipeline_plan(config)
    indent = 2 if args.pretty else None
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
