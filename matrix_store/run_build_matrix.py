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
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-freeze-id")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--write-matrix-version-manifest", action="store_true")
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
    parser.add_argument("--feature-set-name", default="ashare_features_v1")
    parser.add_argument("--feature-set-manifest-path")
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
        data_freeze_dir=args.data_freeze_dir,
        data_freeze_id=args.data_freeze_id,
        data_version_manifest_path=args.data_version_manifest_path,
        require_data_freeze=args.require_data_freeze,
        feature_set_name=args.feature_set_name,
        feature_set_manifest_path=args.feature_set_manifest_path,
    )
    if args.validate:
        report = validate_matrix_cache(result.cache_dir)
        result = replace(result, validation_report_path=f"{result.cache_dir}/matrix_validation_report.json")
        payload = result.to_dict() | {"validation": report.to_dict()}
    else:
        payload = result.to_dict()
    if args.write_matrix_version_manifest:
        from pathlib import Path

        metadata_path = Path(result.metadata_path)
        manifest_path = Path(result.cache_dir) / "matrix_version_manifest.json"
        manifest_path.write_text(metadata_path.read_text(encoding="utf-8"), encoding="utf-8")
        payload["matrix_version_manifest_path"] = str(manifest_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
