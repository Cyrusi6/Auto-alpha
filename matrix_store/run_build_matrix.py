"""CLI for building local A-share matrix caches."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace

from .builder import build_matrix_cache
from .validator import validate_matrix_cache


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a local matrix cache from A-share JSONL artifacts.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--fields")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="same_day_after_close")
    parser.add_argument("--min-listing-days", type=int, default=0)
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--active-mask-path")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument(
        "--target-return-mode",
        choices=("adjusted_close", "raw_close", "corporate_action_total_return"),
        default="adjusted_close",
    )
    parser.add_argument("--corporate-action-dir")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    fields = [item.strip() for item in args.fields.split(",") if item.strip()] if args.fields else None
    result = build_matrix_cache(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        fields=fields,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        min_listing_days=args.min_listing_days,
        exclude_st=args.exclude_st,
        active_mask_path=args.active_mask_path,
        corporate_action_aware=args.corporate_action_aware,
        target_return_mode=args.target_return_mode,
        corporate_action_dir=args.corporate_action_dir,
    )
    if args.validate:
        report = validate_matrix_cache(result.cache_dir)
        result = replace(result, validation_report_path=f"{result.cache_dir}/matrix_validation_report.json")
        payload = result.to_dict() | {"validation": report.to_dict()}
    else:
        payload = result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
