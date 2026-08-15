"""CLI for offline, non-admissible local development bundles."""

from __future__ import annotations

import argparse
import json
import sys

from .local_development_bundle import (
    LocalDevelopmentBundleError,
    LocalDevelopmentScope,
    build_local_development_bundle,
    validate_local_development_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rehabilitate immutable local A-share observations for development replay "
            "without claiming provider coverage or data admission."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--source-freeze-manifest", required=True)
    build.add_argument("--output-root", required=True)
    build.add_argument("--date-start", default="20120101")
    build.add_argument("--date-end", default="20191231")
    build.add_argument("--index-code", default="000300.SH")
    build.add_argument("--workers", type=int, default=4)
    build.add_argument("--pretty", action="store_true")
    validate = commands.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate.add_argument(
        "--trusted-source-freeze-manifest",
        help="revalidate the bundle against the original immutable Source Freeze",
    )
    validate.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            payload = build_local_development_bundle(
                args.source_freeze_manifest,
                args.output_root,
                scope=LocalDevelopmentScope(
                    date_start=args.date_start,
                    date_end=args.date_end,
                    index_code=args.index_code,
                ),
                workers=args.workers,
            )
        else:
            payload = validate_local_development_bundle(
                args.manifest,
                trusted_source_freeze_manifest=args.trusted_source_freeze_manifest,
            )
    except (LocalDevelopmentBundleError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": "local_development_bundle_error",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2 if args.pretty else None,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    summary = {
        key: payload.get(key)
        for key in (
            "schema_version",
            "generation_id",
            "content_hash",
            "manifest_path",
            "mode",
            "source_generation_id",
            "source_evidence_grade",
            "artifact_root",
            "data_admission_eligible",
            "alpha_search_authorized",
            "blockers",
            "cache_hit",
        )
        if key in payload
    }
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
