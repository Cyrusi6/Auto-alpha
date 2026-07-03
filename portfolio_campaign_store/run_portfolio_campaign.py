"""CLI for portfolio certification campaigns."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .consolidate import consolidate_portfolio_campaign
from .ingest import ingest_certified_factor_pool
from .registry import LocalPortfolioCampaignStore
from .report import write_portfolio_campaign_report
from .scheduler import run_portfolio_campaign


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run portfolio certification campaign workflows.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["ingest", "plan", "run", "consolidate", "bundle", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--portfolio-campaign-store-dir", required=True)
    parser.add_argument("--portfolio-campaign-id")
    parser.add_argument("--certified-factor-pool-path")
    parser.add_argument("--data-dir")
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument("--rank-range")
    parser.add_argument("--family-filter")
    parser.add_argument("--source-filter")
    parser.add_argument("--portfolio-policy-profile", default="sample_lenient_portfolio")
    parser.add_argument("--scenario-profile", default="sample")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--as-of-date", default="20240104")
    parser.add_argument("--max-trials", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-portfolio-ready", action="store_true")
    parser.add_argument("--research-readiness-decision-path")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except Exception as exc:
        payload = {"status": "error", "error": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if payload.get("status") in {"success", "planned", "partial", "blocked", "ready"} else 1


def _run(args: argparse.Namespace) -> dict:
    store = LocalPortfolioCampaignStore(args.portfolio_campaign_store_dir)
    readiness = _readiness(args.research_readiness_decision_path)
    if args.require_portfolio_ready and not readiness["ready"]:
        json_path, md_path = write_portfolio_campaign_report(store, {"blocked_reason": "portfolio readiness not satisfied", "readiness": readiness})
        return {"status": "blocked", "blocked_reason": "portfolio readiness not satisfied", "paths": store.paths() | {"portfolio_certification_campaign_report_path": str(json_path), "portfolio_certification_campaign_report_md_path": str(md_path)}}
    if args.command == "smoke":
        payload = _smoke(args)
    elif args.command == "ingest":
        if not args.certified_factor_pool_path:
            raise ValueError("--certified-factor-pool-path is required")
        payload = ingest_certified_factor_pool(
            args.portfolio_campaign_store_dir,
            args.certified_factor_pool_path,
            portfolio_campaign_id=args.portfolio_campaign_id,
            max_items=args.max_items or None,
            rank_range=args.rank_range,
            family_filter=args.family_filter,
            source_filter=args.source_filter,
            portfolio_policy_profile=args.portfolio_policy_profile,
            scenario_profile=args.scenario_profile,
        )
    elif args.command == "plan":
        payload = run_portfolio_campaign(args.portfolio_campaign_store_dir, output_dir=args.output_dir, max_items=args.max_items or None, dry_run=True)
    elif args.command == "run":
        if args.certified_factor_pool_path and not store.load_items():
            ingest_certified_factor_pool(
                args.portfolio_campaign_store_dir,
                args.certified_factor_pool_path,
                portfolio_campaign_id=args.portfolio_campaign_id,
                max_items=args.max_items or None,
                rank_range=args.rank_range,
                family_filter=args.family_filter,
                source_filter=args.source_filter,
                portfolio_policy_profile=args.portfolio_policy_profile,
                scenario_profile=args.scenario_profile,
            )
        payload = run_portfolio_campaign(
            args.portfolio_campaign_store_dir,
            data_dir=args.data_dir,
            factor_store_dir=args.factor_store_dir,
            output_dir=args.output_dir,
            max_items=args.max_items or None,
            resume=args.resume,
            dry_run=args.dry_run,
            scenario_profile=args.scenario_profile,
            portfolio_policy_profile=args.portfolio_policy_profile,
            index_code=args.index_code,
            as_of_date=args.as_of_date,
            max_trials=args.max_trials,
        )
        if not args.dry_run:
            payload = payload | consolidate_portfolio_campaign(args.portfolio_campaign_store_dir)
    elif args.command in {"consolidate", "bundle"}:
        payload = consolidate_portfolio_campaign(args.portfolio_campaign_store_dir)
    else:
        raise ValueError(f"unsupported command: {args.command}")
    json_path, md_path = write_portfolio_campaign_report(store, {"last_command": args.command})
    payload.setdefault("paths", store.paths())
    payload["paths"] = payload["paths"] | {"portfolio_certification_campaign_report_path": str(json_path), "portfolio_certification_campaign_report_md_path": str(md_path)}
    return payload


def _smoke(args: argparse.Namespace) -> dict:
    root = Path(args.output_dir or args.portfolio_campaign_store_dir)
    root.mkdir(parents=True, exist_ok=True)
    pool = root / "certified_factor_pool.jsonl"
    rows = [
        {
            "certified_factor_pool_id": "cfp_smoke_0001",
            "factor_id": "factor_smoke_0001",
            "formula_hash": "hash_smoke_0001",
            "certification_status": "conditional",
            "validation_score": 1.0,
            "certification_score": 1.5,
            "priority": 1,
            "factor_store_dir": str(root / "factor_store"),
            "selected_for_portfolio_lab": True,
        }
    ]
    pool.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    return ingest_certified_factor_pool(args.portfolio_campaign_store_dir, pool, portfolio_campaign_id=args.portfolio_campaign_id or "portfolio_campaign_smoke", max_items=1)


def _readiness(path: str | None) -> dict:
    if not path:
        return {"ready": True, "status": "not_required"}
    target = Path(path)
    if not target.exists():
        return {"ready": False, "status": "missing", "path": str(target)}
    payload = json.loads(target.read_text(encoding="utf-8"))
    ready = bool(payload.get("portfolio_ready") or payload.get("can_run_portfolio_campaign") or payload.get("ready_for_portfolio"))
    ready = ready or str(payload.get("status")) in {"ready", "portfolio_ready", "ready_for_portfolio", "pass"}
    return {"ready": ready, "status": payload.get("status"), "path": str(target)}


if __name__ == "__main__":
    raise SystemExit(main())
