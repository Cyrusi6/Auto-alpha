"""CLI for data source readiness and smoke validation."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.pipeline import ASHARE_DATASETS

from .models import ProviderReadinessStatus
from .smoke_runner import run_data_source_smoke


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run offline or gated online A-share data source smoke checks.")
    parser.add_argument("--provider", choices=["sample", "tushare"], default="sample")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start-date", default="20240102")
    parser.add_argument("--end-date")
    parser.add_argument("--datasets", default=",".join(ASHARE_DATASETS))
    parser.add_argument("--index-codes", default="000300.SH")
    parser.add_argument("--chunk-days", type=int, default=30)
    parser.add_argument("--mode", choices=["overwrite", "append"], default="overwrite")
    parser.add_argument("--cache", dest="cache", action="store_true")
    parser.add_argument("--no-cache", dest="cache", action="store_false")
    parser.set_defaults(cache=False)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--require-token", action="store_true")
    parser.add_argument(
        "--fake-tushare-scenario",
        choices=[
            "success",
            "permission_denied",
            "rate_limited",
            "missing_fields",
            "empty_response",
            "malformed_payload",
            "network_error",
        ],
    )
    parser.add_argument("--max-requests", type=int, default=20)
    parser.add_argument("--max-records-per-dataset", type=int)
    parser.add_argument("--baseline-data-dir")
    parser.add_argument("--compare-baseline", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    parser.add_argument("--fail-on-warning", action="store_true")
    parser.add_argument("--fail-on-baseline-diff", action="store_true")
    parser.add_argument("--run-incremental-recovery", action="store_true")
    parser.add_argument("--run-online-incremental", action="store_true")
    parser.add_argument("--data-lake-registry-dir")
    parser.add_argument("--write-data-version", action="store_true")
    parser.add_argument("--create-research-freeze", action="store_true")
    parser.add_argument("--freeze-dir")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    datasets = [item.strip() for item in args.datasets.split(",") if item.strip()]
    index_codes = tuple(item.strip() for item in args.index_codes.split(",") if item.strip())
    base = AShareDataConfig.from_env()
    config = replace(
        base,
        provider=args.provider,
        data_dir=Path(args.data_dir),
        start_date=args.start_date,
        end_date=args.end_date,
        index_codes=index_codes or base.index_codes,
    )
    report = run_data_source_smoke(
        config=config,
        output_dir=args.output_dir,
        datasets=datasets,
        mode=args.mode,
        chunk_days=args.chunk_days,
        cache_enabled=args.cache,
        audit_enabled=args.audit,
        validate=args.validate,
        stats=args.stats,
        snapshot=args.snapshot,
        compact=args.compact,
        allow_network=args.allow_network,
        require_token=args.require_token,
        fake_tushare_scenario=args.fake_tushare_scenario,
        max_requests=args.max_requests,
        max_records_per_dataset=args.max_records_per_dataset,
        baseline_data_dir=args.baseline_data_dir,
        compare_baseline=args.compare_baseline,
        run_incremental_recovery=args.run_incremental_recovery,
        run_online_incremental=args.run_online_incremental,
        data_lake_registry_dir=args.data_lake_registry_dir,
        write_data_version=args.write_data_version,
        create_data_freeze=args.create_research_freeze,
        freeze_dir=args.freeze_dir,
    )
    payload = report.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    if args.fail_on_baseline_diff and report.baseline_compare is not None and report.baseline_compare.has_differences:
        return 1
    if args.fail_on_error and report.status == ProviderReadinessStatus.ERROR:
        return 1
    if args.fail_on_warning and report.status in {ProviderReadinessStatus.ERROR, ProviderReadinessStatus.WARNING}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
