"""CLI for building local A-share universes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from data_pipeline.ashare.storage import LocalAshareStorage

from .builder import build_universe_from_storage
from .models import UniverseBuildConfig


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        config = UniverseBuildConfig(
            universe_name=args.universe_name,
            as_of_date=args.as_of_date,
            min_listed_days=args.min_listed_days,
            min_amount=args.min_amount,
            exchanges=_parse_csv(args.exchanges),
            boards=_parse_csv(args.boards),
        )
        result = build_universe_from_storage(LocalAshareStorage(args.data_dir), config)
    except SystemExit as exc:
        return int(exc.code)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    indent = 2 if args.pretty else None
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=indent))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an A-share universe from local datasets.")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--universe-name", required=True)
    parser.add_argument("--min-listed-days", type=int, default=60)
    parser.add_argument("--min-amount", type=float, default=0.0)
    parser.add_argument("--exchanges", help="Optional comma-separated exchange filter.")
    parser.add_argument("--boards", help="Optional comma-separated board filter.")
    parser.add_argument("--pretty", action="store_true")
    return parser


def _parse_csv(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    parsed = tuple(item.strip() for item in value.split(",") if item.strip())
    return parsed or None


if __name__ == "__main__":
    raise SystemExit(main())
