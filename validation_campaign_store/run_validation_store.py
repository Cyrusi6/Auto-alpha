"""CLI for validation campaign warehousing and orchestration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .certification_queue import build_certification_queue
from .consolidate import consolidate_validation_results
from .ingest import ingest_candidate_pool
from .leaderboard import build_validation_leaderboard
from .registry import LocalValidationCampaignStore
from .report import write_validation_campaign_report
from .scheduler import plan_validation_shards, run_validation_shards


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run validation campaign store workflows.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["ingest", "plan", "run", "consolidate", "leaderboard", "queue", "smoke"]:
        sp = sub.add_parser(name)
        _add_common_args(sp)
    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--validation-campaign-store-dir", required=True)
    parser.add_argument("--validation-campaign-id")
    parser.add_argument("--source-candidate-pool-path")
    parser.add_argument("--alpha-experiment-store-dir")
    parser.add_argument("--data-dir")
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--max-candidates", type=int, default=0)
    parser.add_argument("--max-candidates-per-shard", type=int, default=0)
    parser.add_argument("--candidate-rank-range")
    parser.add_argument("--family-filter")
    parser.add_argument("--source-filter")
    parser.add_argument("--split-method", default="simple_walk_forward")
    parser.add_argument("--run-multiple-testing", action="store_true")
    parser.add_argument("--run-overfit-risk", action="store_true")
    parser.add_argument("--run-placebo", action="store_true")
    parser.add_argument("--placebo-trials", type=int, default=12)
    parser.add_argument("--run-regime", action="store_true")
    parser.add_argument("--run-sensitivity", action="store_true")
    parser.add_argument("--run-stress-backtest", action="store_true")
    parser.add_argument("--use-compute-scheduler", action="store_true")
    parser.add_argument("--compute-state-dir")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-validation-ready", action="store_true")
    parser.add_argument("--research-readiness-decision-path")
    parser.add_argument("--top-k", type=int, default=100)
    parser.add_argument("--top-k-certification-queue", type=int, default=20)
    parser.add_argument("--certification-policy-profile", default="sample_lenient_certification")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except Exception as exc:
        payload = {"status": "error", "error": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if payload.get("status") in {"success", "warning", "planned", "partial", "blocked"} else 1


def _run(args: argparse.Namespace) -> dict:
    store = LocalValidationCampaignStore(args.validation_campaign_store_dir)
    readiness = _validation_readiness(args.research_readiness_decision_path)
    if args.require_validation_ready and not readiness["ready"]:
        json_path, md_path = write_validation_campaign_report(store, {"blocked_reason": "research readiness does not allow validation", "readiness": readiness})
        return {
            "status": "blocked",
            "blocked_reason": "research readiness does not allow validation",
            "readiness": readiness,
            "paths": store.paths() | {"validation_campaign_store_report_path": str(json_path), "validation_campaign_store_report_md_path": str(md_path)},
        }
    if args.command == "ingest":
        _require(args.source_candidate_pool_path, "--source-candidate-pool-path")
        payload = ingest_candidate_pool(
            args.validation_campaign_store_dir,
            args.source_candidate_pool_path,
            validation_campaign_id=args.validation_campaign_id,
            max_candidates=args.max_candidates or None,
            rank_range=args.candidate_rank_range,
            family_filter=args.family_filter,
            source_filter=args.source_filter,
            shard_count=args.shard_count,
            split_method=args.split_method,
        )
    elif args.command == "plan":
        campaign_id = _campaign_id(args, store)
        shards = plan_validation_shards(
            args.validation_campaign_store_dir,
            args.output_dir or args.validation_campaign_store_dir,
            validation_campaign_id=campaign_id,
            shard_count=args.shard_count,
            max_candidates_per_shard=args.max_candidates_per_shard or None,
        )
        payload = {"status": "planned", "validation_campaign_id": campaign_id, "shard_count": len(shards), "paths": store.paths()}
    elif args.command == "run":
        if args.source_candidate_pool_path and not store.load_candidates():
            ingest_candidate_pool(
                args.validation_campaign_store_dir,
                args.source_candidate_pool_path,
                validation_campaign_id=args.validation_campaign_id,
                max_candidates=args.max_candidates or None,
                shard_count=args.shard_count,
                split_method=args.split_method,
            )
        payload = run_validation_shards(
            args.validation_campaign_store_dir,
            data_dir=args.data_dir or str(Path(args.data_freeze_dir or "") / "data"),
            factor_store_dir=args.factor_store_dir or "",
            output_dir=args.output_dir or args.validation_campaign_store_dir,
            validation_campaign_id=_campaign_id(args, store),
            shard_count=args.shard_count,
            max_candidates_per_shard=args.max_candidates_per_shard or None,
            split_method=args.split_method,
            run_multiple_testing=args.run_multiple_testing,
            run_overfit_risk=args.run_overfit_risk,
            run_placebo=args.run_placebo,
            placebo_trials=args.placebo_trials,
            run_regime=args.run_regime,
            run_sensitivity=args.run_sensitivity,
            run_stress_backtest=args.run_stress_backtest,
            resume=args.resume,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            payload = payload | consolidate_validation_results(args.validation_campaign_store_dir)
            leaderboard = build_validation_leaderboard(args.validation_campaign_store_dir, top_k=args.top_k)
            queue = build_certification_queue(
                args.validation_campaign_store_dir,
                top_k=args.top_k_certification_queue,
                certification_policy_profile=args.certification_policy_profile,
            )
            payload.update({"leaderboard_count": len(leaderboard), "certification_queue_count": len(queue)})
    elif args.command == "consolidate":
        payload = consolidate_validation_results(args.validation_campaign_store_dir)
    elif args.command == "leaderboard":
        leaderboard = build_validation_leaderboard(args.validation_campaign_store_dir, top_k=args.top_k)
        payload = {"status": "success", "leaderboard_count": len(leaderboard), "paths": store.paths()}
    elif args.command == "queue":
        queue = build_certification_queue(args.validation_campaign_store_dir, top_k=args.top_k_certification_queue, certification_policy_profile=args.certification_policy_profile)
        payload = {"status": "success", "certification_queue_count": len(queue), "paths": store.paths()}
    elif args.command == "smoke":
        payload = _smoke(args)
    else:
        raise ValueError(f"unsupported command: {args.command}")
    json_path, md_path = write_validation_campaign_report(store, {"last_command": args.command})
    payload.setdefault("paths", store.paths())
    payload["paths"] = payload["paths"] | {"validation_campaign_store_report_path": str(json_path), "validation_campaign_store_report_md_path": str(md_path)}
    return payload


def _smoke(args: argparse.Namespace) -> dict:
    root = Path(args.output_dir or args.validation_campaign_store_dir)
    pool = root / "alpha_validation_candidate_pool.jsonl"
    pool.parent.mkdir(parents=True, exist_ok=True)
    with pool.open("w", encoding="utf-8") as handle:
        for idx in range(2):
            handle.write(
                json.dumps(
                    {
                        "factor_id": f"factor_smoke_{idx}",
                        "formula_hash": f"hash_smoke_{idx}",
                        "formula_names": ["RET_1D"],
                        "feature_version": "ashare_features_v1",
                        "source_campaign": "smoke_alpha",
                        "rank": idx + 1,
                        "final_score": 1.0 - idx * 0.1,
                        "factor_store_dir": str(root / "factor_store"),
                        "factor_values_path": str(root / "factor_store" / "factor_values" / f"factor_smoke_{idx}.jsonl"),
                        "family": "return",
                    },
                    sort_keys=True,
                )
                + "\n"
            )
    return ingest_candidate_pool(args.validation_campaign_store_dir, pool, validation_campaign_id=args.validation_campaign_id or "validation_campaign_smoke", shard_count=args.shard_count, max_candidates=2)


def _campaign_id(args: argparse.Namespace, store: LocalValidationCampaignStore) -> str:
    if args.validation_campaign_id:
        return args.validation_campaign_id
    campaigns = store.load_campaigns()
    if campaigns:
        return str(campaigns[-1].get("validation_campaign_id"))
    return "validation_campaign"


def _validation_readiness(path: str | None) -> dict:
    if not path:
        return {"ready": True, "status": "not_required", "path": None}
    target = Path(path)
    if not target.exists():
        return {"ready": False, "status": "missing", "path": str(target)}
    payload = json.loads(target.read_text(encoding="utf-8"))
    ready = bool(payload.get("can_run_validation") or payload.get("validation_ready") or payload.get("can_run_validation_lab"))
    ready = ready or str(payload.get("status", "")) in {"validation_ready", "ready_for_validation", "ready", "pass"}
    return {"ready": ready, "status": payload.get("status", ""), "path": str(target)}


def _require(value, name: str) -> None:
    if not value:
        raise ValueError(f"{name} is required")


if __name__ == "__main__":
    raise SystemExit(main())
