"""CLI for one-click A-share research suites."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .models import ResearchSuiteConfig
from .workflow import ResearchSuiteRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a full local A-share research suite.")
    parser.add_argument("--config-json")
    parser.add_argument("--write-default-config")
    parser.add_argument("--suite-name", default="sample_suite")
    parser.add_argument("--provider", default="sample")
    parser.add_argument("--data-dir")
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-freeze-id")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--freeze-validation-report-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--real-data-profile-path")
    parser.add_argument("--require-real-data-freeze", action="store_true")
    parser.add_argument("--real-data-sla-report-path")
    parser.add_argument("--require-real-data-sla-pass", action="store_true")
    parser.add_argument("--matrix-refresh-report-path")
    parser.add_argument("--create-data-version", action="store_true")
    parser.add_argument("--data-lake-registry-dir")
    parser.add_argument("--create-research-freeze", action="store_true")
    parser.add_argument("--research-freeze-dir")
    parser.add_argument("--freeze-mode", choices=["copy", "hardlink", "manifest_only"], default="copy")
    parser.add_argument("--validate-data-freeze", action="store_true")
    parser.add_argument("--fail-on-freeze-error", action="store_true")
    parser.add_argument("--use-compute-scheduler", action="store_true")
    parser.add_argument("--compute-state-dir")
    parser.add_argument("--compute-output-dir")
    parser.add_argument("--experiment-output-dir")
    parser.add_argument("--experiment-workflow", default="full_research_compute_smoke")
    parser.add_argument("--gpu-count", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--formula-shards", type=int, default=1)
    parser.add_argument("--use-ddp-pretrain", action="store_true")
    parser.add_argument("--pretrain-world-size", type=int, default=1)
    parser.add_argument("--max-parallel-gpu-jobs", type=int, default=1)
    parser.add_argument("--max-parallel-cpu-jobs", type=int, default=1)
    parser.add_argument("--resource-report-dir")
    parser.add_argument("--resume-compute", action="store_true")
    parser.add_argument("--compute-dry-run", action="store_true")
    parser.add_argument("--run-alpha-factory", action="store_true")
    parser.add_argument("--alpha-factory-dir")
    parser.add_argument("--alpha-campaign-name", default="suite_alpha_campaign")
    parser.add_argument("--alpha-candidate-budget", type=int, default=36)
    parser.add_argument("--alpha-template-budget", type=int, default=10)
    parser.add_argument("--alpha-random-budget", type=int, default=10)
    parser.add_argument("--alpha-mutation-budget", type=int, default=8)
    parser.add_argument("--alpha-crossover-budget", type=int, default=4)
    parser.add_argument("--alpha-corpus-budget", type=int, default=8)
    parser.add_argument("--alpha-neural-budget", type=int, default=0)
    parser.add_argument("--alpha-feature-set-name", default="ashare_features_v1")
    parser.add_argument("--alpha-build-feature-set", action="store_true")
    parser.add_argument("--alpha-feature-output-dir")
    parser.add_argument("--alpha-use-batch-eval", action="store_true")
    parser.add_argument("--alpha-use-compute-scheduler", action="store_true")
    parser.add_argument("--alpha-shard-count", type=int, default=1)
    parser.add_argument("--alpha-top-k", type=int, default=8)
    parser.add_argument("--alpha-max-per-family", type=int, default=3)
    parser.add_argument("--alpha-min-novelty-score", type=float, default=0.0)
    parser.add_argument("--alpha-register-shortlist", action="store_true")
    parser.add_argument("--use-alpha-shortlist-for-search", action="store_true")
    parser.add_argument("--run-validation-lab", action="store_true")
    parser.add_argument("--validation-lab-dir")
    parser.add_argument(
        "--validation-split-method",
        choices=["simple_walk_forward", "rolling_walk_forward", "anchored_walk_forward", "purged_embargo", "cscv"],
        default="simple_walk_forward",
    )
    parser.add_argument("--validation-train-size", type=int, default=1)
    parser.add_argument("--validation-size", type=int, default=0)
    parser.add_argument("--validation-test-size", type=int, default=1)
    parser.add_argument("--validation-step-size", type=int, default=1)
    parser.add_argument("--validation-embargo-size", type=int, default=0)
    parser.add_argument("--validation-cscv-groups", type=int, default=2)
    parser.add_argument("--validation-max-cscv-combinations", type=int, default=6)
    parser.add_argument("--run-multiple-testing", action="store_true")
    parser.add_argument("--run-overfit-risk", action="store_true")
    parser.add_argument("--run-placebo", action="store_true")
    parser.add_argument("--placebo-trials", type=int, default=12)
    parser.add_argument("--run-regime-validation", action="store_true")
    parser.add_argument("--run-sensitivity-validation", action="store_true")
    parser.add_argument("--run-stress-backtest-validation", action="store_true")
    parser.add_argument("--run-validation-campaign-store", action="store_true")
    parser.add_argument("--validation-campaign-store-dir")
    parser.add_argument("--validation-candidate-pool-path")
    parser.add_argument("--validation-campaign-shard-count", type=int, default=1)
    parser.add_argument("--validation-campaign-max-candidates", type=int, default=0)
    parser.add_argument("--validation-campaign-top-k-certification", type=int, default=20)
    parser.add_argument("--write-factor-certification-queue", action="store_true")
    parser.add_argument("--run-factor-certification", action="store_true")
    parser.add_argument("--factor-certification-dir")
    parser.add_argument("--certification-policy-path")
    parser.add_argument(
        "--certification-policy-profile",
        choices=["sample_lenient_certification", "research_standard", "production_strict"],
        default="sample_lenient_certification",
    )
    parser.add_argument("--require-certification", action="store_true")
    parser.add_argument("--fail-on-certification-rejected", action="store_true")
    parser.add_argument("--run-portfolio-lab", action="store_true")
    parser.add_argument("--portfolio-lab-dir")
    parser.add_argument("--portfolio-lab-scenario-profile", default="sample")
    parser.add_argument("--portfolio-policy-grid-path")
    parser.add_argument("--portfolio-methods", default="equal_weight,risk_aware")
    parser.add_argument("--portfolio-risk-aversions", default="0.5,1.0")
    parser.add_argument("--portfolio-turnover-penalties", default="0.0,0.1")
    parser.add_argument("--portfolio-benchmark-weights", default="1.0")
    parser.add_argument("--portfolio-max-weight-values", default="0.10")
    parser.add_argument("--portfolio-max-names-values", default="2,20")
    parser.add_argument("--portfolio-max-turnover-values", default="1.0")
    parser.add_argument("--portfolio-max-tracking-error-values", default="1.0")
    parser.add_argument("--portfolio-top-n-values", default="2,20")
    parser.add_argument("--run-portfolio-certification", action="store_true")
    parser.add_argument("--portfolio-certification-dir")
    parser.add_argument("--portfolio-certification-policy-path")
    parser.add_argument(
        "--portfolio-certification-policy-profile",
        choices=["sample_lenient_portfolio", "research_standard", "research_standard_portfolio", "production_strict", "production_strict_portfolio"],
        default="sample_lenient_portfolio",
    )
    parser.add_argument("--require-portfolio-certification", action="store_true")
    parser.add_argument("--fail-on-portfolio-certification-rejected", action="store_true")
    parser.add_argument("--create-portfolio-policy-approval", action="store_true")
    parser.add_argument("--portfolio-policy-approval-store-dir")
    parser.add_argument("--register-optimizer-policy", action="store_true")
    parser.add_argument("--universe-name", default="csi300_sample")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--report-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--backtest-dir")
    parser.add_argument("--orders-dir")
    parser.add_argument("--as-of-date", default="20240104")
    parser.add_argument("--factor-transform", default="winsorize_zscore")
    parser.add_argument("--search-mode", choices=["random", "neural", "hybrid"], default="random")
    parser.add_argument("--search-seed", type=int, default=42)
    parser.add_argument("--search-population-size", type=int, default=12)
    parser.add_argument("--search-generations", type=int, default=2)
    parser.add_argument("--search-max-candidates", type=int)
    parser.add_argument("--neural-warmup-steps", type=int, default=1)
    parser.add_argument("--neural-policy-steps", type=int, default=1)
    parser.add_argument("--neural-checkpoint")
    parser.add_argument("--hybrid-neural-ratio", type=float, default=0.5)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--composite-method", default="rank_average")
    parser.add_argument("--portfolio-method", choices=["equal_weight", "risk_aware"], default="equal_weight")
    parser.add_argument("--risk-aversion", type=float, default=1.0)
    parser.add_argument("--turnover-penalty", type=float, default=0.1)
    parser.add_argument("--max-turnover", type=float, default=1.0)
    parser.add_argument("--max-industry-active-weight", type=float, default=0.20)
    parser.add_argument("--max-tracking-error", type=float, default=1.0)
    parser.add_argument("--use-factor-risk-model", action="store_true")
    parser.add_argument("--risk-model-lookback", type=int)
    parser.add_argument("--risk-model-shrinkage", type=float, default=0.1)
    parser.add_argument("--attribution", action="store_true")
    parser.add_argument("--max-style-exposure", type=float)
    parser.add_argument("--max-active-style-exposure", type=float)
    parser.add_argument("--max-factor-risk-contribution", type=float)
    parser.add_argument("--promote-latest-composite", action="store_true")
    parser.add_argument("--skip-data-sync", action="store_true")
    parser.add_argument("--skip-universe", action="store_true")
    parser.add_argument("--skip-orders", action="store_true")
    parser.add_argument("--disable-promotion", action="store_true")
    parser.add_argument("--walk-forward-train-size", type=int, default=1)
    parser.add_argument("--walk-forward-test-size", type=int, default=1)
    parser.add_argument("--walk-forward-step-size", type=int, default=1)
    parser.add_argument("--build-matrix-cache", action="store_true")
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--use-matrix-cache", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--benchmark-dir")
    parser.add_argument("--build-formula-corpus", action="store_true")
    parser.add_argument("--formula-corpus-dir")
    parser.add_argument("--pretrain-alphagpt", action="store_true")
    parser.add_argument("--pretrain-dir")
    parser.add_argument("--pretrain-epochs", type=int, default=1)
    parser.add_argument("--pretrain-batch-size", type=int, default=8)
    parser.add_argument("--pretrain-max-sequences", type=int)
    parser.add_argument("--pretrain-device", default="auto")
    parser.add_argument("--preference-epochs", type=int, default=0)
    parser.add_argument("--use-batch-eval", action="store_true")
    parser.add_argument("--batch-eval-dir")
    parser.add_argument("--batch-eval-chunk-size", type=int, default=32)
    parser.add_argument("--batch-eval-device", default="auto")
    parser.add_argument("--use-eval-cache", action="store_true")
    parser.add_argument("--eval-cache-dir")
    parser.add_argument("--register-model-version", action="store_true")
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--create-model-review-package", action="store_true")
    parser.add_argument("--model-lifecycle-output-dir")
    parser.add_argument("--require-model-approval", action="store_true")
    parser.add_argument("--model-lifecycle-policy-path")
    parser.add_argument("--model-approval-store-dir")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="same_day_after_close")
    parser.add_argument("--min-listing-days", type=int, default=0)
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--run-pit-validation", action="store_true")
    parser.add_argument("--pit-output-dir")
    parser.add_argument("--run-leakage-audit", action="store_true")
    parser.add_argument("--leakage-audit-dir")
    parser.add_argument("--fail-on-pit-blocker", action="store_true")
    parser.add_argument("--fail-on-leakage-blocker", action="store_true")
    parser.add_argument("--include-corporate-actions", action="store_true", default=True)
    parser.add_argument("--no-corporate-actions", action="store_true")
    parser.add_argument("--corporate-action-output-dir")
    parser.add_argument("--run-corporate-action-report", action="store_true")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument(
        "--target-return-mode",
        choices=["adjusted_close", "raw_close", "corporate_action_total_return"],
        default="adjusted_close",
    )
    parser.add_argument("--corporate-action-dir")
    parser.add_argument("--corporate-action-cash-field", default="cash_div")
    parser.add_argument("--corporate-action-application-date-mode", default="pay_date")
    parser.add_argument("--reconcile-adjustment-factors", action="store_true")
    parser.add_argument("--fail-on-corporate-action-error", action="store_true")
    parser.add_argument("--settlement-aware", action="store_true")
    parser.add_argument("--settlement-dir")
    parser.add_argument(
        "--settlement-profile",
        choices=["cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"],
        default="cn_ashare_paper_default",
    )
    parser.add_argument("--cost-basis-method", choices=["average", "fifo"], default="average")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.write_default_config:
        config = _default_config(args)
        path = Path(args.write_default_config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"config_path": str(path)}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0

    config = _load_config(args.config_json) if args.config_json else _default_config(args)
    result = ResearchSuiteRunner(config).run()
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.status == "success" else 1


def _load_config(path: str) -> ResearchSuiteConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ResearchSuiteConfig(**payload)


def _default_config(args: argparse.Namespace) -> ResearchSuiteConfig:
    base_dir = Path(args.output_dir).parent if args.output_dir else Path("/tmp/auto-alpha-suite")
    return ResearchSuiteConfig(
        suite_name=args.suite_name,
        data_dir=str(args.data_dir or base_dir / "data"),
        data_freeze_dir=args.data_freeze_dir,
        data_freeze_id=args.data_freeze_id,
        data_version_manifest_path=args.data_version_manifest_path,
        freeze_validation_report_path=args.freeze_validation_report_path,
        require_data_freeze=args.require_data_freeze or args.require_real_data_freeze,
        create_data_version=args.create_data_version,
        data_lake_registry_dir=args.data_lake_registry_dir,
        create_research_freeze=args.create_research_freeze,
        research_freeze_dir=args.research_freeze_dir,
        freeze_mode=args.freeze_mode,
        validate_data_freeze=args.validate_data_freeze,
        fail_on_freeze_error=args.fail_on_freeze_error,
        use_compute_scheduler=args.use_compute_scheduler,
        compute_state_dir=args.compute_state_dir,
        compute_output_dir=args.compute_output_dir,
        experiment_output_dir=args.experiment_output_dir,
        experiment_workflow=args.experiment_workflow,
        gpu_count=args.gpu_count,
        shard_count=args.shard_count,
        formula_shards=args.formula_shards,
        use_ddp_pretrain=args.use_ddp_pretrain,
        pretrain_world_size=args.pretrain_world_size,
        max_parallel_gpu_jobs=args.max_parallel_gpu_jobs,
        max_parallel_cpu_jobs=args.max_parallel_cpu_jobs,
        resource_report_dir=args.resource_report_dir,
        resume_compute=args.resume_compute,
        compute_dry_run=args.compute_dry_run,
        run_alpha_factory=args.run_alpha_factory,
        alpha_factory_dir=args.alpha_factory_dir,
        alpha_campaign_name=args.alpha_campaign_name,
        alpha_candidate_budget=args.alpha_candidate_budget,
        alpha_template_budget=args.alpha_template_budget,
        alpha_random_budget=args.alpha_random_budget,
        alpha_mutation_budget=args.alpha_mutation_budget,
        alpha_crossover_budget=args.alpha_crossover_budget,
        alpha_corpus_budget=args.alpha_corpus_budget,
        alpha_neural_budget=args.alpha_neural_budget,
        alpha_feature_set_name=args.alpha_feature_set_name,
        alpha_build_feature_set=args.alpha_build_feature_set,
        alpha_feature_output_dir=args.alpha_feature_output_dir,
        alpha_use_batch_eval=args.alpha_use_batch_eval,
        alpha_use_compute_scheduler=args.alpha_use_compute_scheduler,
        alpha_shard_count=args.alpha_shard_count,
        alpha_top_k=args.alpha_top_k,
        alpha_max_per_family=args.alpha_max_per_family,
        alpha_min_novelty_score=args.alpha_min_novelty_score,
        alpha_register_shortlist=args.alpha_register_shortlist,
        use_alpha_shortlist_for_search=args.use_alpha_shortlist_for_search,
        run_validation_lab=args.run_validation_lab,
        validation_lab_dir=args.validation_lab_dir,
        validation_split_method=args.validation_split_method,
        validation_train_size=args.validation_train_size,
        validation_size=args.validation_size,
        validation_test_size=args.validation_test_size,
        validation_step_size=args.validation_step_size,
        validation_embargo_size=args.validation_embargo_size,
        validation_cscv_groups=args.validation_cscv_groups,
        validation_max_cscv_combinations=args.validation_max_cscv_combinations,
        run_multiple_testing=args.run_multiple_testing,
        run_overfit_risk=args.run_overfit_risk,
        run_placebo=args.run_placebo,
        placebo_trials=args.placebo_trials,
        run_regime_validation=args.run_regime_validation,
        run_sensitivity_validation=args.run_sensitivity_validation,
        run_stress_backtest_validation=args.run_stress_backtest_validation,
        run_validation_campaign_store=args.run_validation_campaign_store,
        validation_campaign_store_dir=args.validation_campaign_store_dir,
        validation_candidate_pool_path=args.validation_candidate_pool_path,
        validation_campaign_shard_count=args.validation_campaign_shard_count,
        validation_campaign_max_candidates=args.validation_campaign_max_candidates,
        validation_campaign_top_k_certification=args.validation_campaign_top_k_certification,
        write_factor_certification_queue=args.write_factor_certification_queue,
        run_factor_certification=args.run_factor_certification,
        factor_certification_dir=args.factor_certification_dir,
        certification_policy_path=args.certification_policy_path,
        certification_policy_profile=args.certification_policy_profile,
        require_certification=args.require_certification,
        fail_on_certification_rejected=args.fail_on_certification_rejected,
        run_portfolio_lab=args.run_portfolio_lab,
        portfolio_lab_dir=args.portfolio_lab_dir,
        portfolio_lab_scenario_profile=args.portfolio_lab_scenario_profile,
        portfolio_policy_grid_path=args.portfolio_policy_grid_path,
        portfolio_methods=args.portfolio_methods,
        portfolio_risk_aversions=args.portfolio_risk_aversions,
        portfolio_turnover_penalties=args.portfolio_turnover_penalties,
        portfolio_benchmark_weights=args.portfolio_benchmark_weights,
        portfolio_max_weight_values=args.portfolio_max_weight_values,
        portfolio_max_names_values=args.portfolio_max_names_values,
        portfolio_max_turnover_values=args.portfolio_max_turnover_values,
        portfolio_max_tracking_error_values=args.portfolio_max_tracking_error_values,
        portfolio_top_n_values=args.portfolio_top_n_values,
        run_portfolio_certification=args.run_portfolio_certification,
        portfolio_certification_dir=args.portfolio_certification_dir,
        portfolio_certification_policy_path=args.portfolio_certification_policy_path,
        portfolio_certification_policy_profile=args.portfolio_certification_policy_profile,
        require_portfolio_certification=args.require_portfolio_certification,
        fail_on_portfolio_certification_rejected=args.fail_on_portfolio_certification_rejected,
        create_portfolio_policy_approval=args.create_portfolio_policy_approval,
        portfolio_policy_approval_store_dir=args.portfolio_policy_approval_store_dir,
        register_optimizer_policy=args.register_optimizer_policy,
        universe_name=args.universe_name,
        index_code=args.index_code,
        factor_store_dir=str(args.factor_store_dir or base_dir / "store"),
        report_dir=str(args.report_dir or base_dir / "reports"),
        output_dir=str(args.output_dir or base_dir / "suite"),
        backtest_dir=str(args.backtest_dir or base_dir / "backtest"),
        orders_dir=str(args.orders_dir or base_dir / "orders"),
        provider=args.provider,
        as_of_date=args.as_of_date,
        factor_transform=args.factor_transform,
        search_mode=args.search_mode,
        search_seed=args.search_seed,
        search_population_size=args.search_population_size,
        search_generations=args.search_generations,
        search_max_candidates=args.search_max_candidates,
        neural_warmup_steps=args.neural_warmup_steps,
        neural_policy_steps=args.neural_policy_steps,
        neural_checkpoint=args.neural_checkpoint,
        hybrid_neural_ratio=args.hybrid_neural_ratio,
        top_k=args.top_k,
        composite_method=args.composite_method,
        portfolio_method=args.portfolio_method,
        risk_aversion=args.risk_aversion,
        turnover_penalty=args.turnover_penalty,
        max_turnover=args.max_turnover,
        max_industry_active_weight=args.max_industry_active_weight,
        max_tracking_error=args.max_tracking_error,
        use_factor_risk_model=args.use_factor_risk_model,
        risk_model_lookback=args.risk_model_lookback,
        risk_model_shrinkage=args.risk_model_shrinkage,
        attribution=args.attribution,
        max_style_exposure=args.max_style_exposure,
        max_active_style_exposure=args.max_active_style_exposure,
        max_factor_risk_contribution=args.max_factor_risk_contribution,
        promote_latest_composite=args.promote_latest_composite,
        pretty=args.pretty,
        skip_data_sync=args.skip_data_sync,
        skip_universe=args.skip_universe,
        skip_orders=args.skip_orders,
        disable_promotion=args.disable_promotion,
        walk_forward_train_size=args.walk_forward_train_size,
        walk_forward_test_size=args.walk_forward_test_size,
        walk_forward_step_size=args.walk_forward_step_size,
        build_matrix_cache=args.build_matrix_cache,
        matrix_cache_dir=args.matrix_cache_dir,
        use_matrix_cache=args.use_matrix_cache,
        benchmark=args.benchmark,
        benchmark_dir=args.benchmark_dir,
        build_formula_corpus=args.build_formula_corpus,
        formula_corpus_dir=args.formula_corpus_dir,
        pretrain_alphagpt=args.pretrain_alphagpt,
        pretrain_dir=args.pretrain_dir,
        pretrain_epochs=args.pretrain_epochs,
        pretrain_batch_size=args.pretrain_batch_size,
        pretrain_max_sequences=args.pretrain_max_sequences,
        pretrain_device=args.pretrain_device,
        pretrain_preference_steps=args.preference_epochs,
        use_batch_eval=args.use_batch_eval,
        batch_eval_dir=args.batch_eval_dir,
        batch_eval_chunk_size=args.batch_eval_chunk_size,
        batch_eval_device=args.batch_eval_device,
        use_eval_cache=args.use_eval_cache,
        eval_cache_dir=args.eval_cache_dir,
        register_model_version=args.register_model_version,
        model_registry_dir=args.model_registry_dir,
        create_model_review_package=args.create_model_review_package,
        model_lifecycle_output_dir=args.model_lifecycle_output_dir,
        require_model_approval=args.require_model_approval,
        model_lifecycle_policy_path=args.model_lifecycle_policy_path,
        model_approval_store_dir=args.model_approval_store_dir,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        min_listing_days=args.min_listing_days,
        exclude_st=args.exclude_st,
        run_pit_validation=args.run_pit_validation,
        pit_output_dir=args.pit_output_dir,
        run_leakage_audit=args.run_leakage_audit,
        leakage_audit_dir=args.leakage_audit_dir,
        fail_on_pit_blocker=args.fail_on_pit_blocker,
        fail_on_leakage_blocker=args.fail_on_leakage_blocker,
        include_corporate_actions=not args.no_corporate_actions,
        corporate_action_output_dir=args.corporate_action_output_dir,
        run_corporate_action_report=args.run_corporate_action_report,
        corporate_action_aware=args.corporate_action_aware,
        target_return_mode=args.target_return_mode,
        corporate_action_dir=args.corporate_action_dir,
        corporate_action_cash_field=args.corporate_action_cash_field,
        corporate_action_application_date_mode=args.corporate_action_application_date_mode,
        reconcile_adjustment_factors=args.reconcile_adjustment_factors,
        fail_on_corporate_action_error=args.fail_on_corporate_action_error,
        settlement_aware=args.settlement_aware,
        settlement_dir=args.settlement_dir,
        settlement_profile=args.settlement_profile,
        cost_basis_method=args.cost_basis_method,
    )


if __name__ == "__main__":
    raise SystemExit(main())
