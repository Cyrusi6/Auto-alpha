"""CLI for gated real-data backfill, data lake and matrix refresh runs."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path

from data_pipeline.ashare.config import AShareDataConfig

from .env_file import load_env_file
from .pipeline import run_real_data_pipeline
from .profiles import get_real_data_profile, load_profile_json, profile_with_overrides


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run governed real-data operations.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("readiness", "run", "resume", "report", "smoke"):
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile-name", default="sample_offline_small")
    parser.add_argument("--profile-json")
    parser.add_argument("--provider")
    parser.add_argument("--env-file")
    parser.add_argument("--data-dir", required=False)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--staging-dir")
    parser.add_argument("--cache-dir")
    parser.add_argument("--data-lake-registry-dir")
    parser.add_argument("--freeze-dir")
    parser.add_argument("--freeze-name")
    parser.add_argument("--freeze-mode", choices=("copy", "manifest_only"))
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--datasets")
    parser.add_argument("--index-codes")
    parser.add_argument("--security-list-statuses")
    parser.add_argument("--chunk-days", type=int, default=30)
    parser.add_argument("--chunk-strategy")
    parser.add_argument("--mode", choices=("overwrite", "append"), default="append")
    parser.add_argument("--cache", action="store_true")
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
    parser.add_argument("--build-matrix", action="store_true")
    parser.add_argument("--refresh-matrix", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--require-token", action="store_true")
    parser.add_argument("--fake-tushare-scenario")
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--rate-limit-per-minute", type=int)
    parser.add_argument("--disable-rate-limit", action="store_true")
    parser.add_argument("--token-expiry")
    parser.add_argument("--run-pit-validation", action="store_true")
    parser.add_argument("--run-leakage-audit", action="store_true")
    parser.add_argument("--run-corporate-actions-report", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    profile = load_profile_json(args.profile_json) if args.profile_json else get_real_data_profile(args.profile_name)
    effective_provider = args.provider or profile.provider
    if args.allow_network and effective_provider == "tushare" and not args.fake_tushare_scenario:
        print(json.dumps({"status": "blocked", "reason": "superseded_by_task055j"}, sort_keys=True))
        return 2
    datasets = _csv(args.datasets) or profile.datasets
    index_codes = _csv(args.index_codes) or profile.index_codes
    statuses = _csv(args.security_list_statuses) or profile.security_list_statuses
    profile = profile_with_overrides(
        profile,
        provider=args.provider,
        datasets=datasets,
        start_date=args.start_date,
        end_date=args.end_date,
        index_codes=index_codes,
        security_list_statuses=statuses,
        chunk_strategy=args.chunk_strategy,
        max_requests=args.max_requests,
        rate_limit_per_minute=args.rate_limit_per_minute,
        allow_network=args.allow_network,
        require_token=args.require_token,
        storage_mode=args.mode,
        freeze_mode=args.freeze_mode,
    )
    env_values = load_env_file(args.env_file)
    env = {**env_values, **os.environ}
    config = AShareDataConfig.from_env(env)
    config = replace(
        config,
        provider=effective_provider,
        data_dir=Path(args.data_dir or env.get("ASHARE_REAL_DATA_ROOT") or env.get("ASHARE_DATA_DIR") or "data/ashare"),
        start_date=args.start_date or profile.start_date,
        end_date=args.end_date or profile.end_date,
        index_codes=tuple(index_codes),
        security_list_statuses=tuple(statuses),
        tushare_api_url=env.get("TUSHARE_API_URL") or profile.api_url or config.tushare_api_url,
        tushare_token=env.get("TUSHARE_TOKEN") or config.tushare_token,
    )
    if args.command == "smoke":
        config = replace(config, provider="sample", data_dir=Path(args.data_dir or Path(args.output_dir) / "data"), start_date="20240102", end_date="20240104")
        profile = get_real_data_profile("sample_offline_small")
    run = run_real_data_pipeline(
        profile=profile,
        config=config,
        data_dir=args.data_dir or str(config.data_dir),
        output_dir=args.output_dir,
        staging_dir=args.staging_dir,
        cache_dir=args.cache_dir,
        data_lake_registry_dir=args.data_lake_registry_dir,
        freeze_dir=args.freeze_dir,
        freeze_name=args.freeze_name,
        freeze_mode=args.freeze_mode,
        matrix_cache_dir=args.matrix_cache_dir,
        chunk_days=args.chunk_days,
        mode=args.mode,
        cache=args.cache,
        audit=args.audit,
        resume=args.resume or args.command == "resume",
        validate=args.validate or args.command == "smoke",
        stats=args.stats or args.command == "smoke",
        compact=args.compact,
        snapshot=args.snapshot,
        direct_append=args.direct_append,
        trade_days_only=args.trade_days_only,
        trade_day_datasets=_csv(args.trade_day_datasets),
        financial_by_ts_code=args.financial_by_ts_code,
        financial_ts_codes=_csv_or_file(args.financial_ts_codes),
        ts_code_split_datasets=_csv(args.ts_code_split_datasets),
        build_matrix=args.build_matrix or args.command == "smoke",
        refresh_matrix=args.refresh_matrix or args.command == "smoke",
        allow_network=args.allow_network,
        require_token=args.require_token,
        fake_tushare_scenario=args.fake_tushare_scenario,
        max_requests=args.max_requests,
        rate_limit_per_minute=args.rate_limit_per_minute,
        disable_rate_limit=args.disable_rate_limit,
        token_expiry=args.token_expiry,
        command_name=args.command,
    )
    payload = run.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if run.status not in {"failed"} else 1


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()] if value else []


def _csv_or_file(value: str | None) -> list[str]:
    if not value:
        return []
    path = Path(value)
    if path.exists():
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return _csv(value)


if __name__ == "__main__":
    raise SystemExit(main())
