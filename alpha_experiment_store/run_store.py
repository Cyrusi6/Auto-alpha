"""CLI for the local Alpha experiment warehouse."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from factor_store import FactorRecord, LocalFactorStore

from .consolidate import consolidate_factor_stores
from .ingest import ingest_alpha_factory_run
from .leaderboard import build_leaderboard_from_factor_store, write_validation_candidate_pool
from .models import AlphaExperimentRecord, AlphaShardRecord
from .registry import LocalAlphaExperimentStore
from .report import write_store_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local Alpha Factory experiment store artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register")
    register.add_argument("--store-dir", required=True)
    register.add_argument("--experiment-id", required=True)
    register.add_argument("--campaign-id", required=True)
    register.add_argument("--campaign-name", default="alpha_campaign")
    register.add_argument("--status", default="registered")
    register.add_argument("--pretty", action="store_true")

    ingest = sub.add_parser("ingest")
    _add_store_args(ingest)
    ingest.add_argument("--alpha-factory-report-path")
    ingest.add_argument("--alpha-campaign-manifest-path")
    ingest.add_argument("--experiment-id")
    ingest.add_argument("--shard-factor-store-dir", action="append", default=[])
    ingest.add_argument("--consolidate-shards", action="store_true")
    ingest.add_argument("--consolidated-factor-store-dir")
    ingest.add_argument("--write-leaderboard", action="store_true")
    ingest.add_argument("--validation-candidate-pool-dir")
    ingest.add_argument("--leaderboard-top-k", type=int, default=100)
    ingest.add_argument("--max-validation-candidates", type=int, default=50)
    ingest.add_argument("--pretty", action="store_true")

    consolidate = sub.add_parser("consolidate")
    _add_store_args(consolidate)
    consolidate.add_argument("--shard-factor-store-dir", action="append", required=True)
    consolidate.add_argument("--output-factor-store-dir", required=True)
    consolidate.add_argument("--experiment-id", default="")
    consolidate.add_argument("--campaign-id", default="")
    consolidate.add_argument("--write-leaderboard", action="store_true")
    consolidate.add_argument("--validation-candidate-pool-dir")
    consolidate.add_argument("--leaderboard-top-k", type=int, default=100)
    consolidate.add_argument("--max-validation-candidates", type=int, default=50)
    consolidate.add_argument("--pretty", action="store_true")

    leaderboard = sub.add_parser("leaderboard")
    _add_store_args(leaderboard)
    leaderboard.add_argument("--factor-store-dir", required=True)
    leaderboard.add_argument("--top-k", type=int, default=100)
    leaderboard.add_argument("--validation-candidate-pool-dir")
    leaderboard.add_argument("--max-validation-candidates", type=int, default=50)
    leaderboard.add_argument("--pretty", action="store_true")

    smoke = sub.add_parser("smoke")
    smoke.add_argument("--output-dir", required=True)
    smoke.add_argument("--pretty", action="store_true")
    return parser


def _add_store_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--store-dir", required=True)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "register":
        store = LocalAlphaExperimentStore(args.store_dir)
        store.register_experiment(
            AlphaExperimentRecord(
                experiment_id=args.experiment_id,
                campaign_id=args.campaign_id,
                campaign_name=args.campaign_name,
                status=args.status,
            )
        )
        json_path, _md_path = write_store_report(store)
        payload = {"status": "success", "alpha_experiment_store_report_path": str(json_path)}
    elif args.command == "ingest":
        payload = ingest_alpha_factory_run(
            args.store_dir,
            campaign_report_path=args.alpha_factory_report_path,
            campaign_manifest_path=args.alpha_campaign_manifest_path,
            shard_factor_store_dirs=args.shard_factor_store_dir,
            experiment_id=args.experiment_id,
            consolidate_shards=args.consolidate_shards,
            consolidated_factor_store_dir=args.consolidated_factor_store_dir,
            write_leaderboard_flag=args.write_leaderboard,
            validation_candidate_pool_dir=args.validation_candidate_pool_dir,
            leaderboard_top_k=args.leaderboard_top_k,
            max_validation_candidates=args.max_validation_candidates,
        )
    elif args.command == "consolidate":
        payload = _run_consolidate(args)
    elif args.command == "leaderboard":
        payload = _run_leaderboard(args)
    elif args.command == "smoke":
        payload = _run_smoke(args.output_dir)
    else:
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None, sort_keys=getattr(args, "pretty", False)))
    return 0 if payload.get("status") in {"success", "warning"} else 1


def _run_consolidate(args: argparse.Namespace) -> dict:
    report = consolidate_factor_stores(
        args.shard_factor_store_dir,
        args.output_factor_store_dir,
        experiment_id=args.experiment_id,
        campaign_id=args.campaign_id,
        report_dir=args.store_dir,
    )
    store = LocalAlphaExperimentStore(args.store_dir)
    store.write_consolidated_factors(report.get("consolidated_factors", []))
    paths = {"alpha_factor_dedup_report_path": report.get("alpha_factor_dedup_report_path", "")}
    if args.write_leaderboard:
        leaderboard = build_leaderboard_from_factor_store(args.output_factor_store_dir, top_k=args.leaderboard_top_k, campaign_id=args.campaign_id)
        store.write_leaderboard(leaderboard)
        pool_dir = args.validation_candidate_pool_dir or args.store_dir
        pool_path, pool_rows = write_validation_candidate_pool(
            leaderboard,
            pool_dir,
            max_candidates=args.max_validation_candidates,
            factor_store_dir=args.output_factor_store_dir,
        )
        store.write_validation_candidate_pool(pool_rows)
        paths.update({"alpha_leaderboard_path": str(store.leaderboard_path), "alpha_validation_candidate_pool_path": str(pool_path)})
    report_json, report_md = write_store_report(store, {"dedup_report": report})
    return {
        "status": report["status"],
        "merged_factor_count": report["merged_factor_count"],
        "unique_formula_count": report["unique_formula_count"],
        "duplicate_count": report["duplicate_count"],
        "paths": paths | {
            "alpha_experiment_store_report_path": str(report_json),
            "alpha_experiment_store_report_md_path": str(report_md),
        },
    }


def _run_leaderboard(args: argparse.Namespace) -> dict:
    store = LocalAlphaExperimentStore(args.store_dir)
    leaderboard = build_leaderboard_from_factor_store(args.factor_store_dir, top_k=args.top_k)
    store.write_leaderboard(leaderboard)
    pool_path = None
    if args.validation_candidate_pool_dir:
        pool_path, pool_rows = write_validation_candidate_pool(
            leaderboard,
            args.validation_candidate_pool_dir,
            max_candidates=args.max_validation_candidates,
            factor_store_dir=args.factor_store_dir,
        )
        store.write_validation_candidate_pool(pool_rows)
    report_json, _ = write_store_report(store)
    return {
        "status": "success",
        "leaderboard_count": len(leaderboard),
        "validation_candidate_pool_path": str(pool_path) if pool_path else "",
        "alpha_experiment_store_report_path": str(report_json),
    }


def _run_smoke(output_dir: str | Path) -> dict:
    root = Path(output_dir)
    shard_dirs = [root / "shards" / "shard_0000" / "factor_store", root / "shards" / "shard_0001" / "factor_store"]
    for idx, store_dir in enumerate(shard_dirs):
        _write_fake_factor_store(store_dir, idx)
    store_dir = root / "store"
    merged_store = root / "merged_factor_store"
    store = LocalAlphaExperimentStore(store_dir)
    store.register_experiment(
        AlphaExperimentRecord(
            experiment_id="alpha_store_smoke",
            campaign_id="alpha_store_smoke",
            campaign_name="alpha_store_smoke",
            candidate_budget=4,
            shard_count=2,
            status="success",
        )
    )
    for idx, shard_dir in enumerate(shard_dirs):
        store.register_shard(
            AlphaShardRecord(
                shard_id=f"alpha_store_smoke_shard_{idx:04d}",
                experiment_id="alpha_store_smoke",
                shard_index=idx,
                shard_count=2,
                formula_count=2,
                evaluated_count=2,
                approved_count=1,
                factor_store_dir=str(shard_dir),
                status="success",
            )
        )
    report = consolidate_factor_stores(shard_dirs, merged_store, experiment_id="alpha_store_smoke", campaign_id="alpha_store_smoke", report_dir=store_dir)
    store.write_consolidated_factors(report.get("consolidated_factors", []))
    leaderboard = build_leaderboard_from_factor_store(merged_store, top_k=10, campaign_id="alpha_store_smoke")
    store.write_leaderboard(leaderboard)
    pool_path, pool_rows = write_validation_candidate_pool(leaderboard, store_dir, max_candidates=4, factor_store_dir=str(merged_store))
    store.write_validation_candidate_pool(pool_rows)
    report_json, report_md = write_store_report(store, {"dedup_report": report})
    return {
        "status": "success",
        "merged_factor_count": report["merged_factor_count"],
        "leaderboard_count": len(leaderboard),
        "validation_candidate_count": len(pool_rows),
        "paths": {
            "alpha_experiment_store_report_path": str(report_json),
            "alpha_experiment_store_report_md_path": str(report_md),
            "alpha_validation_candidate_pool_path": str(pool_path),
            "consolidated_factor_store_dir": str(merged_store),
        },
    }


def _write_fake_factor_store(store_dir: Path, idx: int) -> None:
    store = LocalFactorStore(store_dir)
    rows = [
        ("duplicate_hash", "factor_duplicate_a" if idx == 0 else "factor_duplicate_b", "approved" if idx == 1 else "candidate", 0.7 + idx * 0.1),
        (f"unique_hash_{idx}", f"factor_unique_{idx}", "approved", 0.5 + idx * 0.1),
    ]
    for formula_hash, factor_id, status, score in rows:
        store.save_factor(
            FactorRecord(
                factor_id=factor_id,
                formula=["RET_1D"],
                formula_tokens=[0],
                formula_hash=formula_hash,
                feature_version="ashare_features_v1",
                operator_version="ashare_ops_v1",
                lookback_days=1,
                created_at="2026-07-03T00:00:00Z",
                status=status,
                metrics={"score": score, "coverage": 1.0, "turnover": 0.1},
                metadata={"formula_complexity": 1, "novelty_score": 0.2, "alpha_family_tags": ["return"]},
            )
        )
        store.save_factor_values(factor_id, ["000001.SZ", "000002.SZ"], ["20240102", "20240103"], [[1.0, 2.0], [2.0, 3.0]])


if __name__ == "__main__":
    raise SystemExit(main())
