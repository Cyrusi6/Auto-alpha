"""CLI for matrix cache refresh and freshness validation."""

from __future__ import annotations

import argparse
import json

from .refresh import run_matrix_refresh


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh or validate local matrix caches.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "refresh", "validate", "smoke"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--data-dir", required=True)
        cmd.add_argument("--data-freeze-dir")
        cmd.add_argument("--data-version-manifest-path")
        cmd.add_argument("--matrix-cache-dir", required=True)
        cmd.add_argument("--output-dir", required=True)
        cmd.add_argument("--universe-name")
        cmd.add_argument("--universe-file")
        cmd.add_argument("--point-in-time", action="store_true")
        cmd.add_argument("--feature-cutoff-mode", default="same_day_after_close")
        cmd.add_argument("--corporate-action-aware", action="store_true")
        cmd.add_argument(
            "--target-return-mode",
            choices=("adjusted_close", "raw_close", "corporate_action_total_return"),
            default="adjusted_close",
        )
        cmd.add_argument("--refresh-mode", choices=("skip_if_fresh", "validate_only", "full_rebuild"), default="skip_if_fresh")
        cmd.add_argument("--require-data-freeze", action="store_true")
        cmd.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    refresh_mode = "validate_only" if args.command in {"plan", "validate"} else args.refresh_mode
    result = run_matrix_refresh(
        data_dir=args.data_dir,
        data_freeze_dir=args.data_freeze_dir,
        data_version_manifest_path=args.data_version_manifest_path,
        matrix_cache_dir=args.matrix_cache_dir,
        output_dir=args.output_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        corporate_action_aware=args.corporate_action_aware,
        target_return_mode=args.target_return_mode,
        refresh_mode=refresh_mode,
        require_data_freeze=args.require_data_freeze,
        pretty_config={"command": args.command},
    )
    payload = result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if result.status not in {"failed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
