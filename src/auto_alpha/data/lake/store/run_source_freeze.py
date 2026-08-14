"""CLI for immutable A-share Source Freeze and independent data admission."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .source_freeze import (
    SourceFreezeConfig,
    audit_source_freeze_sources,
    build_source_freeze_generation,
    validate_source_freeze_generation,
    validate_physical_research_view,
)
from .admission import DataAdmissionScope, verify_data_admission


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an A-share Source Freeze and independently verify data admission."
    )
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
    validate.add_argument("--pretty", action="store_true")
    view = subparsers.add_parser("validate-research-view")
    view.add_argument("--manifest", required=True)
    view.add_argument("--pretty", action="store_true")
    admission = subparsers.add_parser("verify-admission")
    admission.add_argument("--profile-manifest", required=True)
    admission.add_argument("--source-generation-manifest", required=True)
    admission.add_argument("--access-view", required=True)
    admission.add_argument("--date-start", required=True)
    admission.add_argument("--date-end", required=True)
    admission.add_argument("--as-of-market-date", required=True)
    admission.add_argument("--verdict-root", required=True)
    admission.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"preflight", "build"}:
        config = SourceFreezeConfig(
            governed_root=str(Path(args.governed_root).resolve()),
            output_root=str(Path(args.output_root).resolve()),
            source_cutoff=str(args.source_cutoff),
            batch_rows=int(args.batch_rows),
            sample_size=int(args.sample_size),
            workers=max(1, int(args.workers)),
        )
        payload = (
            audit_source_freeze_sources(config)
            if args.command == "preflight"
            else build_source_freeze_generation(config)
        )
    elif args.command == "validate":
        payload = validate_source_freeze_generation(args.manifest)
    elif args.command == "validate-research-view":
        payload = validate_physical_research_view(args.manifest)
    else:
        verdict = verify_data_admission(
            args.profile_manifest,
            args.source_generation_manifest,
            DataAdmissionScope(
                access_view=args.access_view,
                date_start=args.date_start,
                date_end=args.date_end,
                as_of_market_date=args.as_of_market_date,
            ),
            args.verdict_root,
        )
        payload = verdict.to_dict()
    print(json.dumps(_summary(payload), ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    if args.command == "verify-admission" and payload.get("outcome") != "admitted":
        return 2
    return 0


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in (
            "schema_version",
            "verdict_id",
            "outcome",
            "profile_id",
            "source_generation_id",
            "coverage_plan_content_hash",
            "coverage_root",
            "data_scope_root",
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
