"""CLI for Alpha Factory campaigns."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .models import AlphaCampaignConfig
from .runner import AlphaFactoryRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Alpha Factory candidate campaigns.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init-campaign", "generate", "static-check", "proxy-eval", "full-eval", "score", "shortlist", "run", "resume", "report", "smoke"):
        cmd = sub.add_parser(name)
        _add_common_args(cmd)
    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--campaign-name", default="alpha_factory_campaign")
    parser.add_argument("--campaign-id")
    parser.add_argument("--data-dir", required=False, default="data/ashare")
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--real-data-profile-path")
    parser.add_argument("--require-real-data-freeze", action="store_true")
    parser.add_argument("--real-data-sla-report-path")
    parser.add_argument("--require-real-data-sla-pass", action="store_true")
    parser.add_argument("--matrix-refresh-report-path")
    parser.add_argument("--factor-store-dir", default="artifacts/factor_store")
    parser.add_argument("--formula-corpus-path")
    parser.add_argument("--candidates-json")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report-dir")
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--feature-set-name", default="ashare_features_v1")
    parser.add_argument("--feature-set-manifest-path")
    parser.add_argument("--build-feature-set", action="store_true")
    parser.add_argument("--feature-output-dir")
    parser.add_argument("--provider", default="sample")
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--factor-transform", default="raw")
    parser.add_argument("--candidate-budget", type=int, default=40)
    parser.add_argument("--template-budget", type=int, default=12)
    parser.add_argument("--random-budget", type=int, default=12)
    parser.add_argument("--mutation-budget", type=int, default=8)
    parser.add_argument("--crossover-budget", type=int, default=4)
    parser.add_argument("--corpus-budget", type=int, default=8)
    parser.add_argument("--neural-budget", type=int, default=0)
    parser.add_argument("--neural-checkpoint")
    parser.add_argument("--max-formula-len", type=int, default=8)
    parser.add_argument("--max-complexity", type=int, default=20)
    parser.add_argument("--max-lookback", type=int, default=20)
    parser.add_argument("--proxy-max-candidates", type=int, default=30)
    parser.add_argument("--proxy-max-dates", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--max-per-family", type=int, default=3)
    parser.add_argument("--min-novelty-score", type=float, default=0.0)
    parser.add_argument("--max-pairwise-correlation", type=float, default=0.99)
    parser.add_argument("--enable-gate", action="store_true")
    parser.add_argument("--correlation-threshold", type=float, default=0.99)
    parser.add_argument("--min-coverage", type=float, default=0.5)
    parser.add_argument("--use-batch-eval", action="store_true")
    parser.add_argument("--batch-eval-dir")
    parser.add_argument("--batch-eval-chunk-size", type=int, default=8)
    parser.add_argument("--batch-eval-device", default="auto")
    parser.add_argument("--use-eval-cache", action="store_true")
    parser.add_argument("--eval-cache-dir")
    parser.add_argument("--use-compute-scheduler", action="store_true")
    parser.add_argument("--compute-state-dir")
    parser.add_argument("--compute-output-dir")
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--max-parallel-gpu-jobs", type=int, default=1)
    parser.add_argument("--max-parallel-cpu-jobs", type=int, default=1)
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="same_day_after_close")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument("--target-return-mode", default="adjusted_close")
    parser.add_argument("--settlement-aware", action="store_true")
    parser.add_argument("--run-leakage-audit", action="store_true")
    parser.add_argument("--leakage-audit-dir")
    parser.add_argument("--register-shortlist", action="store_true")
    parser.add_argument("--refresh-candidates", action="store_true")
    parser.add_argument("--refresh-proxy", action="store_true")
    parser.add_argument("--refresh-eval", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--research-readiness-decision-path")
    parser.add_argument("--require-alpha-factory-ready", action="store_true")
    parser.add_argument("--alpha-experiment-store-dir")
    parser.add_argument("--experiment-id")
    parser.add_argument("--register-experiment", action="store_true")
    parser.add_argument("--consolidate-shards", action="store_true")
    parser.add_argument("--consolidated-factor-store-dir")
    parser.add_argument("--write-leaderboard", action="store_true")
    parser.add_argument("--validation-candidate-pool-dir")
    parser.add_argument("--max-validation-candidates", type=int, default=50)
    parser.add_argument("--leaderboard-top-k", type=int, default=100)
    parser.add_argument("--dedupe-across-campaigns", action="store_true")
    parser.add_argument("--previous-experiment-dir", action="append", default=[])
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = AlphaCampaignConfig(
        campaign_name=args.campaign_name,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        factor_store_dir=args.factor_store_dir,
        report_dir=args.report_dir,
        data_freeze_dir=args.data_freeze_dir,
        data_version_manifest_path=args.data_version_manifest_path,
        require_data_freeze=args.require_data_freeze or args.require_real_data_freeze,
        formula_corpus_path=args.formula_corpus_path,
        candidates_json=args.candidates_json,
        matrix_cache_dir=args.matrix_cache_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        feature_set_name=args.feature_set_name,
        feature_set_manifest_path=args.feature_set_manifest_path,
        build_feature_set=args.build_feature_set,
        feature_output_dir=args.feature_output_dir,
        factor_transform=args.factor_transform,
        candidate_budget=args.candidate_budget,
        template_budget=args.template_budget,
        random_budget=args.random_budget,
        mutation_budget=args.mutation_budget,
        crossover_budget=args.crossover_budget,
        corpus_budget=args.corpus_budget,
        neural_budget=args.neural_budget,
        max_formula_len=args.max_formula_len,
        max_complexity=args.max_complexity,
        max_lookback=args.max_lookback,
        proxy_max_candidates=args.proxy_max_candidates,
        proxy_max_dates=args.proxy_max_dates,
        top_k=args.top_k,
        max_per_family=args.max_per_family,
        min_novelty_score=args.min_novelty_score,
        max_pairwise_correlation=args.max_pairwise_correlation,
        enable_gate=args.enable_gate,
        correlation_threshold=args.correlation_threshold,
        min_coverage=args.min_coverage,
        use_batch_eval=args.use_batch_eval,
        batch_eval_dir=args.batch_eval_dir,
        batch_eval_chunk_size=args.batch_eval_chunk_size,
        batch_eval_device=args.batch_eval_device,
        use_eval_cache=args.use_eval_cache,
        eval_cache_dir=args.eval_cache_dir,
        use_compute_scheduler=args.use_compute_scheduler,
        compute_state_dir=args.compute_state_dir,
        compute_output_dir=args.compute_output_dir,
        shard_count=args.shard_count,
        max_parallel_gpu_jobs=args.max_parallel_gpu_jobs,
        max_parallel_cpu_jobs=args.max_parallel_cpu_jobs,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        corporate_action_aware=args.corporate_action_aware,
        target_return_mode=args.target_return_mode,
        settlement_aware=args.settlement_aware,
        run_leakage_audit=args.run_leakage_audit,
        leakage_audit_dir=args.leakage_audit_dir,
        register_shortlist=args.register_shortlist,
        refresh_candidates=args.refresh_candidates,
        refresh_proxy=args.refresh_proxy,
        refresh_eval=args.refresh_eval,
        resume=args.resume or args.command == "resume",
        seed=args.seed,
        research_readiness_decision_path=args.research_readiness_decision_path,
        require_alpha_factory_ready=args.require_alpha_factory_ready,
        alpha_experiment_store_dir=args.alpha_experiment_store_dir,
        experiment_id=args.experiment_id,
        register_experiment=args.register_experiment,
        consolidate_shards=args.consolidate_shards,
        consolidated_factor_store_dir=args.consolidated_factor_store_dir,
        write_leaderboard=args.write_leaderboard,
        validation_candidate_pool_dir=args.validation_candidate_pool_dir,
        max_validation_candidates=args.max_validation_candidates,
        leaderboard_top_k=args.leaderboard_top_k,
        dedupe_across_campaigns=args.dedupe_across_campaigns,
        previous_experiment_dirs=list(args.previous_experiment_dir or []),
    )
    result = AlphaFactoryRunner(config).run()
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.status in {"success", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
