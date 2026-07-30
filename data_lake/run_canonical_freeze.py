"""CLI for the canonical immutable A-share research freeze."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .canonical_freeze import (
    CanonicalFreezeConfig,
    audit_canonical_freeze_sources,
    build_canonical_research_freeze,
    validate_canonical_research_freeze,
    validate_physical_research_view,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and validate the canonical A-share research freeze.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "build"):
        command = subparsers.add_parser(name)
        command.add_argument("--governed-root", required=True)
        command.add_argument("--output-root", required=True)
        command.add_argument("--source-cutoff", default="20260630")
        command.add_argument("--batch-rows", type=int, default=50_000)
        command.add_argument("--sample-size", type=int, default=1_000)
        command.add_argument("--workers", type=int, default=4)
        command.add_argument("--pretty", action="store_true")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--require-research-ready", action="store_true")
    validate.add_argument("--pretty", action="store_true")
    view = subparsers.add_parser("validate-research-view")
    view.add_argument("--manifest", required=True)
    view.add_argument("--require-research-ready", action="store_true")
    view.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"preflight", "build"}:
        config = CanonicalFreezeConfig(
            governed_root=str(Path(args.governed_root).resolve()),
            output_root=str(Path(args.output_root).resolve()),
            source_cutoff=str(args.source_cutoff),
            batch_rows=int(args.batch_rows),
            sample_size=int(args.sample_size),
            workers=max(1, int(args.workers)),
        )
        payload = (
            audit_canonical_freeze_sources(config)
            if args.command == "preflight"
            else build_canonical_research_freeze(config)
        )
    elif args.command == "validate":
        payload = validate_canonical_research_freeze(args.manifest)
    else:
        payload = validate_physical_research_view(args.manifest)
    print(json.dumps(_summary(payload), ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    if getattr(args, "require_research_ready", False) and not payload.get("alpha_search_authorized"):
        return 2
    return 0


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in (
            "schema_version",
            "status",
            "generation_id",
            "content_hash",
            "source_catalog_hash",
            "partition_root",
            "search_partition_root",
            "alpha_search_authorized",
            "sealed_holdout_historically_observed",
            "sealed_holdout_untouched",
            "certification_ready",
            "blockers",
            "warnings",
            "report_path",
            "manifest_path",
            "search_view_manifest_path",
        )
        if key in payload
    }


if __name__ == "__main__":
    raise SystemExit(main())
