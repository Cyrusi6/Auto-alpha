"""CLI entry point for the A-share data pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .ashare import ASHARE_DATASETS, AShareDataConfig, build_pipeline_plan
from .ashare.manager import sync_ashare_datasets
from .ashare.quality import validate_all_datasets, write_quality_report
from .ashare.stats import compute_all_dataset_stats, write_dataset_stats
from .ashare.storage import LocalAshareStorage
from .ashare.sync_plan import build_sync_plan


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
        "--plan-only",
        action="store_true",
        help="Print a production sync plan without writing datasets.",
    )
    parser.add_argument(
        "--use-plan",
        action="store_true",
        help="Execute sync through the production sync plan.",
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
    parser.add_argument("--index-codes", help="Comma-separated index codes for index_members sync.")
    parser.add_argument("--security-list-statuses", help="Comma-separated Tushare stock_basic list_status values, e.g. L,D,P.")
    parser.add_argument("--include-corporate-actions", dest="include_corporate_actions", action="store_true")
    parser.add_argument("--no-corporate-actions", dest="include_corporate_actions", action="store_false")
    parser.set_defaults(include_corporate_actions=None)
    parser.add_argument(
        "--corporate-action-query-date-field",
        choices=("ex_date", "ann_date", "record_date", "imp_ann_date"),
        help="Date field used for Tushare dividend queries.",
    )
    parser.add_argument("--corporate-action-apply-statuses", help="Comma-separated implemented statuses.")
    parser.add_argument(
        "--corporate-action-cash-field",
        choices=("cash_div", "cash_div_tax"),
        help="Cash dividend field used by downstream accounting.",
    )
    parser.add_argument("--chunk-days", type=int, default=30, help="Date chunk size for planned sync jobs.")
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
    parser.add_argument(
        "--cache",
        dest="cache_enabled",
        action="store_true",
        help="Enable local Tushare response cache for planned sync.",
    )
    parser.add_argument(
        "--no-cache",
        dest="cache_enabled",
        action="store_false",
        help="Disable local Tushare response cache.",
    )
    parser.set_defaults(cache_enabled=False)
    parser.add_argument("--resume", action="store_true", help="Skip jobs already marked successful in state.")
    parser.add_argument("--validate-only", action="store_true", help="Validate existing local datasets only.")
    parser.add_argument(
        "--fail-on-quality-error",
        action="store_true",
        help="Return non-zero when validation reports errors.",
    )
    parser.add_argument("--compact", action="store_true", help="Compact local datasets after sync or as a standalone action.")
    parser.add_argument("--snapshot", action="store_true", help="Snapshot local datasets after sync or as a standalone action.")
    parser.add_argument("--snapshot-name", help="Optional snapshot folder name.")
    parser.add_argument("--stats", action="store_true", help="Write dataset_stats.json.")
    parser.add_argument("--audit", action="store_true", help="Write api_audit.jsonl for planned provider requests.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        config = _config_from_args(args)
        selected_datasets = _parse_datasets(args.datasets)
        if selected_datasets is None and not config.include_corporate_actions:
            selected_datasets = [dataset for dataset in ASHARE_DATASETS if dataset != "corporate_actions"]
    except SystemExit as exc:
        return int(exc.code)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    indent = 2 if args.pretty else None
    if args.plan_only:
        plan = build_sync_plan(
            config,
            datasets=selected_datasets,
            chunk_days=args.chunk_days,
            index_codes=config.index_codes,
            start_date=config.start_date,
            end_date=config.end_date,
        )
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=indent))
        return 0

    if args.validate_only:
        return _run_validate_only(config, indent=indent, fail_on_quality_error=args.fail_on_quality_error)

    if not args.sync and (args.compact or args.snapshot or args.stats):
        return _run_local_governance_actions(
            config,
            selected_datasets=selected_datasets,
            compact=args.compact,
            snapshot=args.snapshot,
            snapshot_name=args.snapshot_name,
            stats=args.stats,
            indent=indent,
        )

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
            use_plan=args.use_plan,
            chunk_days=args.chunk_days,
            cache_enabled=args.cache_enabled,
            audit_enabled=args.audit,
            resume=args.resume,
            fail_on_quality_error=args.fail_on_quality_error,
            compact_after_sync=args.compact,
            snapshot_after_sync=args.snapshot,
            snapshot_name=args.snapshot_name,
            write_stats=args.stats,
        )
    except (NotImplementedError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=indent))
    if args.fail_on_quality_error and result.has_errors:
        return 3
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
    if args.index_codes is not None:
        overrides["index_codes"] = tuple(item.strip() for item in args.index_codes.split(",") if item.strip())
    if args.security_list_statuses is not None:
        overrides["security_list_statuses"] = tuple(
            item.strip().upper() for item in args.security_list_statuses.split(",") if item.strip()
        )
    if args.include_corporate_actions is not None:
        overrides["include_corporate_actions"] = bool(args.include_corporate_actions)
    if args.corporate_action_query_date_field is not None:
        overrides["corporate_action_query_date_field"] = args.corporate_action_query_date_field
    if args.corporate_action_apply_statuses is not None:
        overrides["corporate_action_apply_statuses"] = tuple(
            item.strip() for item in args.corporate_action_apply_statuses.split(",") if item.strip()
        )
    if args.corporate_action_cash_field is not None:
        overrides["corporate_action_cash_field"] = args.corporate_action_cash_field

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


def _run_validate_only(config: AShareDataConfig, indent: int | None, fail_on_quality_error: bool) -> int:
    storage = LocalAshareStorage(config.data_dir)
    report = validate_all_datasets(storage)
    path = write_quality_report(report, storage.data_dir / "quality_report.json")
    payload = report.to_dict()
    payload["quality_report_path"] = str(path)
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    if fail_on_quality_error and payload["has_errors"]:
        return 3
    return 0


def _run_local_governance_actions(
    config: AShareDataConfig,
    selected_datasets: list[str] | None,
    compact: bool,
    snapshot: bool,
    snapshot_name: str | None,
    stats: bool,
    indent: int | None,
) -> int:
    storage = LocalAshareStorage(config.data_dir)
    selected = list(ASHARE_DATASETS if selected_datasets is None else selected_datasets)
    payload: dict[str, object] = {"data_dir": str(config.data_dir)}

    if compact:
        compacted = [storage.compact_dataset(dataset) for dataset in selected if storage.dataset_exists(dataset)]
        payload["compaction_summary"] = [
            {"dataset": result.dataset, "path": result.path, "records": result.records}
            for result in compacted
        ]

    if snapshot:
        snapshot_paths = [
            storage.snapshot_dataset(dataset, snapshot_name=snapshot_name)
            for dataset in selected
            if storage.dataset_exists(dataset)
        ]
        payload["snapshot_paths"] = [str(path) for path in snapshot_paths]

    if stats:
        stats_path = write_dataset_stats(compute_all_dataset_stats(storage), storage.data_dir / "dataset_stats.json")
        payload["stats_path"] = str(stats_path)

    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
