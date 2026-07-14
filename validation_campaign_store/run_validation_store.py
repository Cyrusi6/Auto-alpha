"""CLI for validation campaign warehousing and orchestration."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

from artifact_schema.writer import write_json_artifact

from .certification_queue import build_certification_queue
from .artifacts import resolve_campaign_artifacts
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
    parser.add_argument("--source-campaign-root")
    parser.add_argument("--source-candidate-pool-path")
    parser.add_argument("--alpha-experiment-store-dir")
    parser.add_argument("--data-dir")
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--feature-set-manifest-path")
    parser.add_argument("--feature-tensor-path")
    parser.add_argument("--feature-validity-tensor-path")
    parser.add_argument("--snapshot-proof-manifest-path")
    parser.add_argument("--campaign-manifest-path")
    parser.add_argument("--feature-promotion-policy-path")
    parser.add_argument("--feature-promotion-allowlist-path")
    parser.add_argument("--feature-promotion-denylist-path")
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--max-candidates", type=int, default=0)
    parser.add_argument("--max-candidates-per-shard", type=int, default=0)
    parser.add_argument("--candidate-rank-range")
    parser.add_argument("--family-filter")
    parser.add_argument("--source-filter")
    parser.add_argument("--split-method", default="rolling_walk_forward")
    parser.add_argument("--validation-policy", default="real_long_history_engineering_robustness_v2")
    parser.add_argument("--train-size", type=int, default=756)
    parser.add_argument("--validation-size", type=int, default=126)
    parser.add_argument("--test-size", type=int, default=126)
    parser.add_argument("--step-size", type=int, default=126)
    parser.add_argument("--embargo-size", type=int, default=0)
    parser.add_argument("--label-horizon", type=int, default=1)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--research-end-date")
    parser.add_argument("--holdout-start-date")
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
    parser.add_argument("--task-052a-replay", action="store_true")
    parser.add_argument("--task-053a-replay", action="store_true")
    parser.add_argument("--task-054a-replay", action="store_true")
    parser.add_argument("--replay-readiness-path")
    parser.add_argument("--replay-generation-label", default="primary")
    parser.add_argument("--replay-reference-evidence-path")
    parser.add_argument("--force-uncached-replay", action="store_true")
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
    if args.source_campaign_root:
        artifacts = resolve_campaign_artifacts(args.source_campaign_root)
        args.source_candidate_pool_path = args.source_candidate_pool_path or artifacts.candidate_pool_path
        args.factor_store_dir = args.factor_store_dir or artifacts.factor_store_dir
        args.data_dir = args.data_dir or artifacts.data_dir
        args.data_freeze_dir = args.data_freeze_dir or artifacts.data_freeze_dir
        args.matrix_cache_dir = args.matrix_cache_dir or artifacts.matrix_cache_dir
        args.feature_set_manifest_path = args.feature_set_manifest_path or artifacts.feature_manifest_path
        args.feature_tensor_path = args.feature_tensor_path or artifacts.feature_tensor_path
        args.campaign_manifest_path = args.campaign_manifest_path or artifacts.campaign_manifest_path
        args.feature_promotion_policy_path = args.feature_promotion_policy_path or artifacts.promotion_policy_path
        args.feature_promotion_allowlist_path = args.feature_promotion_allowlist_path or artifacts.promotion_allowlist_path
        args.feature_promotion_denylist_path = args.feature_promotion_denylist_path or artifacts.promotion_denylist_path
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
            data_freeze_dir=args.data_freeze_dir,
            matrix_cache_dir=args.matrix_cache_dir,
            feature_manifest_path=args.feature_set_manifest_path,
            feature_tensor_path=args.feature_tensor_path,
            feature_validity_tensor_path=args.feature_validity_tensor_path,
            snapshot_proof_manifest_path=args.snapshot_proof_manifest_path,
            campaign_manifest_path=args.campaign_manifest_path,
            promotion_policy_path=args.feature_promotion_policy_path,
            promotion_allowlist_path=args.feature_promotion_allowlist_path,
            promotion_denylist_path=args.feature_promotion_denylist_path,
            device=args.device,
            validation_policy=args.validation_policy,
            train_size=args.train_size,
            validation_size=args.validation_size,
            test_size=args.test_size,
            step_size=args.step_size,
            embargo_size=args.embargo_size,
            label_horizon=args.label_horizon,
            research_end_date=args.research_end_date,
            holdout_start_date=args.holdout_start_date,
            use_compute_scheduler=args.use_compute_scheduler,
            compute_state_dir=args.compute_state_dir,
            run_multiple_testing=args.run_multiple_testing,
            run_overfit_risk=args.run_overfit_risk,
            run_placebo=args.run_placebo,
            placebo_trials=args.placebo_trials,
            run_regime=args.run_regime,
            run_sensitivity=args.run_sensitivity,
            run_stress_backtest=args.run_stress_backtest,
            resume=args.resume,
            dry_run=args.dry_run,
            task_052a_replay=args.task_052a_replay,
            task_053a_replay=args.task_053a_replay,
            task_054a_replay=args.task_054a_replay,
            replay_readiness_path=args.replay_readiness_path,
            replay_generation_label=args.replay_generation_label,
            replay_reference_evidence_path=args.replay_reference_evidence_path,
            force_uncached_replay=args.force_uncached_replay,
        )
        if not args.dry_run:
            run_status = payload.get("status")
            payload = payload | consolidate_validation_results(args.validation_campaign_store_dir)
            if run_status in {"blocked", "partial", "error"}:
                payload["status"] = run_status
            leaderboard = build_validation_leaderboard(args.validation_campaign_store_dir, top_k=args.top_k)
            queue = build_certification_queue(
                args.validation_campaign_store_dir,
                top_k=args.top_k_certification_queue,
                certification_policy_profile=args.certification_policy_profile,
            )
            payload.update({"leaderboard_count": len(leaderboard), "certification_queue_count": len(queue)})
            payload.setdefault("paths", {}).update(_write_engineering_governance_artifacts(args, payload))
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
                        "feature_version": "ashare_feature_factory_v3",
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


def _write_engineering_governance_artifacts(args: argparse.Namespace, payload: dict) -> dict[str, str]:
    artifact_root = Path(args.validation_campaign_store_dir)
    scan_root = Path(args.output_dir or args.validation_campaign_store_dir)
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in scan_root.rglob("materialization_manifest.json")]
    candidate_reports = [json.loads(path.read_text(encoding="utf-8")) for path in scan_root.rglob("validation_candidate_pool_report.json")]
    issue_codes = Counter()
    for path in scan_root.rglob("validation_issues.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                issue_codes[str(json.loads(line).get("code") or "unknown")] += 1
    resource_rows = [dict(report.get("resource_usage") or {}) for report in candidate_reports]
    result_rows = []
    for path in scan_root.rglob("validation_candidate_pool_results.jsonl"):
        result_rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    status_distribution = Counter(str(row.get("status") or "unknown") for row in result_rows)
    proof_path = Path(args.snapshot_proof_manifest_path) if args.snapshot_proof_manifest_path else None
    proof = json.loads(proof_path.read_text(encoding="utf-8")) if proof_path and proof_path.exists() else {}
    historical_proof = bool(proof.get("historical_constituent_proof") or proof.get("proof_valid"))
    universe_mode = "daily_pit_constituents" if historical_proof else "fixed_asof_constituents"
    silent_zero_count = sum(
        row.get("materialization_status") == "success"
        and float((row.get("statistics") or {}).get("nonzero_ratio", 0.0) or 0.0) <= 0.0
        for row in manifests
    )
    correlation = _candidate_correlation_matrix(scan_root, manifests)
    technical_status = str(payload.get("status") or "partial")
    if technical_status == "success":
        technical_status = "completed"
    engineering = {
        "status": technical_status,
        "evidence_level": "retrospective_engineering_only",
        "selection_data_reused": True,
        "untouched_holdout": False,
        "certification_ready": False,
        "portfolio_ready": False,
        "candidate_count": int(payload.get("candidate_count", len(manifests)) or 0),
        "materialization_success_count": sum(row.get("materialization_status") == "success" for row in manifests),
        "materialization_blocked_count": sum(row.get("materialization_status") != "success" for row in manifests),
        "silent_zero_validation_count": silent_zero_count,
        "validation_passed_count": int(payload.get("success_count", 0) or 0),
        "validation_blocked_count": int(payload.get("failed_count", 0) or 0),
        "validation_blocker_count": int(payload.get("validation_blocker_count", 0) or 0),
        "blocker_distribution": dict(sorted(issue_codes.items())),
        "validation_status_distribution": dict(sorted(status_distribution.items())),
        "universe_mode": universe_mode,
        "historical_constituent_proof": historical_proof,
        "survivorship_bias_blocker": not historical_proof,
        "gpu_shards": resource_rows,
        "candidate_correlation": correlation,
        "campaign_multiple_testing": {
            "pbo_distribution": payload.get("pbo_distribution", {}),
            "deflated_ic_distribution": payload.get("deflated_ic_distribution", {}),
            "approximate": True,
            "certification_supported": False,
        },
        "fallback_to_cpu_count": sum(bool(row.get("fallback_to_cpu")) for row in resource_rows),
        "stress_sensitivity_status": "unsupported_without_real_simulator_reruns",
        "certification_queue_count": int(payload.get("certification_queue_count", 0) or 0),
        "portfolio_queue_count": int(payload.get("portfolio_queue_count", 0) or 0),
    }
    engineering_path = write_json_artifact(artifact_root / "engineering_robustness_report.json", engineering, "engineering_robustness_report", "validation_campaign_store")
    dates_path = Path(args.matrix_cache_dir or "") / "trade_dates.json"
    dates = json.loads(dates_path.read_text(encoding="utf-8")) if dates_path.exists() else []
    max_observed_date = max((str(item) for item in dates), default=None)
    proposed_holdout = args.holdout_start_date if args.holdout_start_date and (not max_observed_date or args.holdout_start_date > max_observed_date) else None
    proposed_research_end = args.research_end_date or max_observed_date
    holdout_plan = {
        "status": "planned_not_started" if proposed_holdout else "waiting_for_future_data",
        "research_end_date": proposed_research_end,
        "holdout_start_date": proposed_holdout,
        "max_observed_target_date": max_observed_date,
        "selection_data_reused": True,
        "untouched_holdout": False,
        "evidence_level": "sealed_retrospective_replay",
        "firewall_rule": "targets and metrics after research_end_date are forbidden in generation, proxy, full-eval and shortlist",
        "required_evidence": "daily PIT constituents and untouched holdout",
        "start_factor_search": False,
        "certification_queue_must_remain_empty": True,
        "portfolio_queue_must_remain_empty": True,
    }
    plan_path = write_json_artifact(artifact_root / "clean_holdout_campaign_plan.json", holdout_plan, "clean_holdout_campaign_plan", "validation_campaign_store")
    return {"engineering_robustness_report_path": str(engineering_path), "clean_holdout_campaign_plan_path": str(plan_path)}


def _candidate_correlation_matrix(scan_root: Path, manifests: list[dict]) -> dict:
    successful = [row for row in manifests if row.get("materialization_status") == "success"]
    factor_ids = sorted(str(row.get("factor_id")) for row in successful)
    if not factor_ids:
        return {"factor_ids": [], "matrix": [], "sample_count": 0, "approximate": True}
    by_factor = {str(row.get("factor_id")): row for row in successful}
    first_manifest_path = _find_materialization_manifest(scan_root, factor_ids[0])
    if first_manifest_path is None:
        return {"factor_ids": factor_ids, "matrix": [], "sample_count": 0, "approximate": True, "reason": "materialization paths unavailable"}
    validity = np.load(first_manifest_path.parent / "validity.npy", mmap_mode="r").reshape(-1).astype(bool)
    valid_indices = np.flatnonzero(validity)
    if valid_indices.size > 200_000:
        positions = np.linspace(0, valid_indices.size - 1, 200_000, dtype=np.int64)
        valid_indices = valid_indices[positions]
    rows = []
    for factor_id in factor_ids:
        manifest_path = _find_materialization_manifest(scan_root, factor_id)
        if manifest_path is None:
            continue
        values = np.load(manifest_path.parent / "values.npy", mmap_mode="r").reshape(-1)
        rows.append(np.asarray(values[valid_indices], dtype=np.float32))
    matrix = np.corrcoef(np.stack(rows, axis=0)) if len(rows) > 1 else np.ones((1, 1), dtype=np.float64)
    return {
        "factor_ids": factor_ids,
        "matrix": np.nan_to_num(matrix, nan=0.0).round(8).tolist(),
        "sample_count": int(valid_indices.size),
        "method": "deterministic_common_validity_subsample",
        "approximate": True,
        "certification_supported": False,
    }


def _find_materialization_manifest(scan_root: Path, factor_id: str) -> Path | None:
    factor_dirs = sorted(path for path in scan_root.rglob(factor_id) if path.is_dir())
    for factor_dir in factor_dirs:
        pointer_path = factor_dir / "current_materialization.json"
        if pointer_path.is_file():
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            generation_path = Path(str(pointer.get("generation_path") or ""))
            if generation_path.parts and not generation_path.is_absolute() and ".." not in generation_path.parts:
                manifest_path = factor_dir / generation_path / "materialization_manifest.json"
                if manifest_path.is_file():
                    return manifest_path
        legacy_path = factor_dir / "materialization_manifest.json"
        if legacy_path.is_file():
            return legacy_path
    return None


if __name__ == "__main__":
    raise SystemExit(main())
