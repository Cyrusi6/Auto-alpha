"""CLI for governed A-share backfill planning and execution."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.pipeline import ASHARE_DATASETS

from .chunking import dataset_chunk_days_for_strategy, parse_dataset_chunk_days
from .coverage import analyze_backfill_coverage, write_backfill_coverage
from .executor import execute_backfill_plan
from .models import BackfillPlan
from .planner import build_backfill_plan, write_backfill_plan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and run governed local A-share backfills.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["plan", "execute", "resume", "coverage", "validate", "report", "smoke"]:
        _add_common(sub.add_parser(name))
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", choices=["sample", "tushare"], default="sample")
    parser.add_argument("--fake-tushare-scenario", choices=["success", "permission_denied", "rate_limited", "missing_fields", "empty_response", "malformed_payload", "network_error"])
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--staging-dir")
    parser.add_argument("--cache-dir")
    parser.add_argument("--audit-path")
    parser.add_argument("--plan-path")
    parser.add_argument("--state-path")
    parser.add_argument("--start-date", default="20240102")
    parser.add_argument("--end-date", default="20240104")
    parser.add_argument("--datasets", default=",".join(ASHARE_DATASETS))
    parser.add_argument("--index-codes", default="000300.SH")
    parser.add_argument("--security-list-statuses", default="L")
    parser.add_argument("--corporate-action-query-date-field", default="ex_date")
    parser.add_argument("--chunk-days", type=int, default=30)
    parser.add_argument("--chunk-strategy", default="uniform")
    parser.add_argument("--dataset-chunk-days")
    parser.add_argument("--mode", choices=["overwrite", "append"], default="append")
    parser.add_argument("--cache", dest="cache", action="store_true")
    parser.add_argument("--no-cache", dest="cache", action="store_false")
    parser.set_defaults(cache=False)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--direct-append", action="store_true")
    parser.add_argument("--trade-days-only", action="store_true")
    parser.add_argument("--trade-day-datasets")
    parser.add_argument("--financial-by-ts-code", action="store_true")
    parser.add_argument("--financial-ts-codes")
    parser.add_argument("--ts-code-split-datasets")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--require-token", action="store_true")
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--rate-limit-per-minute", type=int, default=150)
    parser.add_argument("--disable-rate-limit", action="store_true")
    parser.add_argument("--profile-name")
    parser.add_argument("--profile-hash")
    parser.add_argument("--token-expiry")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.provider == "tushare" and args.allow_network and not args.fake_tushare_scenario:
        print(json.dumps({"status": "blocked", "reason": "superseded_by_task055k_transport_broker"}, sort_keys=True))
        return 2
    datasets = [item.strip() for item in args.datasets.split(",") if item.strip()]
    index_codes = tuple(item.strip() for item in args.index_codes.split(",") if item.strip())
    statuses = tuple(item.strip().upper() for item in args.security_list_statuses.split(",") if item.strip())
    base = AShareDataConfig.from_env()
    config = replace(
        base,
        provider=args.provider,
        data_dir=Path(args.data_dir),
        start_date=args.start_date,
        end_date=args.end_date,
        index_codes=index_codes or base.index_codes,
        security_list_statuses=statuses or base.security_list_statuses,
        corporate_action_query_date_field=args.corporate_action_query_date_field,
    )
    if args.command == "coverage":
        plan = _load_or_build_plan(args, config, datasets)
        matrix = analyze_backfill_coverage(config.data_dir, plan)
        paths = write_backfill_coverage(matrix, args.output_dir)
        payload = {"status": "warning" if matrix.gap_count else "ok", "coverage": matrix.to_dict(), "paths": paths}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 1 if args.fail_on_error and matrix.gap_count else 0
    if args.command in {"plan", "validate", "report"}:
        plan = build_backfill_plan(
            config,
            datasets=datasets,
            chunk_days=args.chunk_days,
            chunk_strategy=args.chunk_strategy,
            dataset_chunk_days=_dataset_chunk_days(args),
            trade_dates=_load_trade_dates(config.data_dir, config.start_date, config.end_date) if args.trade_days_only else None,
            trade_day_datasets=_trade_day_datasets(args),
            financial_ts_codes=_financial_ts_codes(args, config.data_dir) if args.financial_by_ts_code else None,
            ts_code_split_datasets=_ts_code_split_datasets(args),
            max_requests=args.max_requests,
        )
        plan_json, plan_md = write_backfill_plan(plan, args.output_dir)
        payload = plan.to_dict() | {"paths": {"backfill_plan_path": str(plan_json), "backfill_plan_md_path": str(plan_md)}}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 0

    plan = _load_or_build_plan(args, config, datasets)
    report = execute_backfill_plan(
        plan,
        config,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        staging_dir=args.staging_dir,
        cache_dir=args.cache_dir,
        state_path=args.state_path,
        mode=args.mode,
        cache_enabled=args.cache,
        audit_enabled=args.audit,
        resume=args.resume or args.command == "resume",
        validate=args.validate,
        write_stats=args.stats,
        compact=args.compact,
        snapshot=args.snapshot,
        direct_append=args.direct_append,
        allow_network=args.allow_network,
        require_token=args.require_token,
        max_requests=args.max_requests,
        rate_limit_per_minute=args.rate_limit_per_minute,
        disable_rate_limit=args.disable_rate_limit,
        profile_name=args.profile_name,
        profile_hash=args.profile_hash,
        token_expiry=args.token_expiry,
        fail_fast=args.fail_fast,
        fake_tushare_scenario=args.fake_tushare_scenario,
        dry_run=args.dry_run or args.command == "smoke" and False,
    )
    payload = report.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 1 if args.fail_on_error and report.status in {"failed", "blocked"} else 0


def _load_or_build_plan(args: argparse.Namespace, config: AShareDataConfig, datasets: list[str]) -> BackfillPlan:
    if args.plan_path and Path(args.plan_path).exists():
        payload = json.loads(Path(args.plan_path).read_text(encoding="utf-8"))
        from .models import BackfillJob, BackfillScope

        scope = BackfillScope(**payload["scope"])
        jobs = [BackfillJob(**job) for job in payload.get("jobs", [])]
        return BackfillPlan(
            plan_id=payload["plan_id"],
            scope=scope,
            jobs=jobs,
            dataset_count=int(payload.get("dataset_count", len(scope.datasets))),
            job_count=int(payload.get("job_count", len(jobs))),
            estimated_request_count=int(payload.get("estimated_request_count", len(jobs))),
            expected_artifacts=list(payload.get("expected_artifacts", [])),
            online_required=bool(payload.get("online_required", config.provider == "tushare")),
            token_required=bool(payload.get("token_required", config.provider == "tushare")),
            max_requests=payload.get("max_requests"),
            created_at=str(payload.get("created_at") or ""),
        )
    return build_backfill_plan(
        config,
        datasets=datasets,
        chunk_days=args.chunk_days,
        chunk_strategy=args.chunk_strategy,
        dataset_chunk_days=_dataset_chunk_days(args),
        trade_dates=_load_trade_dates(config.data_dir, config.start_date, config.end_date) if args.trade_days_only else None,
        trade_day_datasets=_trade_day_datasets(args),
        financial_ts_codes=_financial_ts_codes(args, config.data_dir) if args.financial_by_ts_code else None,
        ts_code_split_datasets=_ts_code_split_datasets(args),
        max_requests=args.max_requests,
    )


def _dataset_chunk_days(args: argparse.Namespace) -> dict[str, int]:
    values = dataset_chunk_days_for_strategy(args.chunk_strategy, args.chunk_days)
    values.update(parse_dataset_chunk_days(args.dataset_chunk_days))
    return values


def _trade_day_datasets(args: argparse.Namespace) -> list[str] | None:
    if not args.trade_day_datasets:
        return None
    return [item.strip() for item in args.trade_day_datasets.split(",") if item.strip()]


def _load_trade_dates(data_dir: Path, start_date: str, end_date: str | None) -> list[str]:
    path = data_dir / "trade_calendar" / "records.jsonl"
    if not path.exists():
        return []
    end = end_date or start_date
    values: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            trade_date = str(payload.get("trade_date") or "")
            if start_date <= trade_date <= end and bool(payload.get("is_open")):
                values.append(trade_date)
    return sorted(set(values))


def _financial_ts_codes(args: argparse.Namespace, data_dir: Path) -> list[str]:
    if args.financial_ts_codes:
        source = Path(args.financial_ts_codes)
        if source.exists():
            return [line.strip() for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [item.strip() for item in args.financial_ts_codes.split(",") if item.strip()]
    path = data_dir / "securities" / "records.jsonl"
    if not path.exists():
        return []
    codes: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            code = str(payload.get("ts_code") or "").strip()
            if code:
                codes.append(code)
    return sorted(set(codes))


def _ts_code_split_datasets(args: argparse.Namespace) -> list[str] | None:
    if not args.ts_code_split_datasets:
        return None
    return [item.strip() for item in args.ts_code_split_datasets.split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
