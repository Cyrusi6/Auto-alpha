"""CLI for search-style formula research."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path

from factor_engine import SUPPORTED_TRANSFORMS
from data_lake import validate_research_input
from neural_search.models import NeuralSearchConfig
from neural_search.trainer import NeuralFormulaTrainer
from research.composite import COMPOSITE_METHODS
from experiment_orchestrator.merge import merge_formula_search_results
from factor_store import LocalFactorStore
from validation_lab.run_validation import main as run_validation_main
from factor_certification.run_certify import main as run_certify_main

from .models import FormulaSearchConfig
from .search import FormulaSearchRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local formula search for A-share factors.")
    parser.add_argument("--search-mode", choices=["random", "neural", "hybrid"], default="random")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-freeze-id")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--freeze-validation-report-path")
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--population-size", type=int, default=20)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--max-formula-len", type=int, default=8)
    parser.add_argument("--max-complexity", type=int, default=20)
    parser.add_argument("--max-lookback", type=int, default=10)
    parser.add_argument("--mutation-rate", type=float, default=0.7)
    parser.add_argument("--crossover-rate", type=float, default=0.3)
    parser.add_argument("--elite-size", type=int, default=5)
    parser.add_argument("--candidate-batch-size", type=int)
    parser.add_argument("--neural-warmup-steps", type=int, default=1)
    parser.add_argument("--neural-policy-steps", type=int, default=1)
    parser.add_argument("--neural-checkpoint")
    parser.add_argument("--hybrid-neural-ratio", type=float, default=0.5)
    parser.add_argument("--corpus-sequence-path")
    parser.add_argument("--corpus-path")
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--use-matrix-cache", action="store_true")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="same_day_after_close")
    parser.add_argument("--min-listing-days", type=int, default=0)
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--run-leakage-audit", action="store_true")
    parser.add_argument("--leakage-audit-dir")
    parser.add_argument("--fail-on-leakage-blocker", action="store_true")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument(
        "--target-return-mode",
        choices=("adjusted_close", "raw_close", "corporate_action_total_return"),
        default="adjusted_close",
    )
    parser.add_argument("--corporate-action-dir")
    parser.add_argument("--corporate-action-cash-field", choices=("cash_div", "cash_div_tax"), default="cash_div")
    parser.add_argument("--use-batch-eval", action="store_true")
    parser.add_argument("--batch-eval-output-dir", "--batch-eval-dir", dest="batch_eval_output_dir")
    parser.add_argument("--batch-eval-chunk-size", type=int, default=32)
    parser.add_argument("--batch-eval-device", default="auto")
    parser.add_argument("--use-eval-cache", action="store_true")
    parser.add_argument("--eval-cache-dir")
    parser.add_argument("--factor-transform", default="raw", choices=sorted(SUPPORTED_TRANSFORMS))
    parser.add_argument("--enable-gate", action="store_true")
    parser.add_argument("--disable-gate", action="store_true")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--composite-method", default="rank_average", choices=sorted(COMPOSITE_METHODS))
    parser.add_argument("--correlation-threshold", type=float, default=0.95)
    parser.add_argument("--min-coverage", type=float, default=0.8)
    parser.add_argument("--train-ratio", type=float, default=0.6)
    parser.add_argument("--valid-ratio", type=float, default=0.2)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--use-compute-scheduler", action="store_true")
    parser.add_argument("--compute-state-dir")
    parser.add_argument("--compute-output-dir")
    parser.add_argument("--formula-shard-count", type=int, default=1)
    parser.add_argument("--formula-shard-id", type=int)
    parser.add_argument("--resource-report-path")
    parser.add_argument("--experiment-id")
    parser.add_argument("--distributed-search", action="store_true")
    parser.add_argument("--merge-search-shards", action="store_true")
    parser.add_argument("--search-shard-dir", action="append", default=[])
    parser.add_argument("--alpha-candidates-path")
    parser.add_argument("--alpha-campaign-manifest-path")
    parser.add_argument("--feature-set-name", default="ashare_features_v1")
    parser.add_argument("--feature-set-manifest-path")
    parser.add_argument("--use-alpha-shortlist-as-seed", action="store_true")
    parser.add_argument("--alpha-seed-top-k", type=int)
    parser.add_argument("--run-validation-lab", action="store_true")
    parser.add_argument("--validation-output-dir")
    parser.add_argument("--run-certification", action="store_true")
    parser.add_argument("--certification-output-dir")
    parser.add_argument("--certification-policy-path")
    parser.add_argument("--certification-policy-profile", default="sample_lenient_certification")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    freeze_payload = _apply_data_freeze_args(args)
    if args.merge_search_shards:
        report = merge_formula_search_results(args.search_shard_dir, args.output_dir)
        payload = report.to_dict()
        payload.update(freeze_payload)
        payload["search_mode"] = "merge"
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0 if report.status in {"success", "warning"} else 1
    search_config = FormulaSearchConfig(
        seed=args.seed,
        population_size=args.population_size,
        generations=args.generations,
        max_formula_len=args.max_formula_len,
        max_complexity=args.max_complexity,
        max_lookback=args.max_lookback,
        mutation_rate=args.mutation_rate,
        crossover_rate=args.crossover_rate,
        elite_size=args.elite_size,
        top_k=args.top_k,
        candidate_batch_size=args.candidate_batch_size,
        search_mode=args.search_mode,
        neural_warmup_steps=args.neural_warmup_steps,
        neural_policy_steps=args.neural_policy_steps,
        neural_checkpoint=args.neural_checkpoint,
        hybrid_neural_ratio=args.hybrid_neural_ratio,
    )
    if args.search_mode == "neural":
        result = _run_neural(args)
        result.update(freeze_payload)
        _attach_pit_metadata(result, args)
        _attach_search_trial_summary(result)
        _maybe_run_validation_and_certification(result, args)
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if args.search_mode == "hybrid":
        result = _run_hybrid(args, search_config)
        result.update(freeze_payload)
        _attach_pit_metadata(result, args)
        _attach_search_trial_summary(result)
        _maybe_run_validation_and_certification(result, args)
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    result = FormulaSearchRunner(
        search_config=search_config,
        data_dir=args.data_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        factor_store_dir=args.factor_store_dir,
        report_dir=args.report_dir,
        output_dir=args.output_dir,
        factor_transform=args.factor_transform,
        enable_gate=args.enable_gate and not args.disable_gate,
        correlation_threshold=args.correlation_threshold,
        min_coverage=args.min_coverage,
        composite_method=args.composite_method,
        train_ratio=args.train_ratio,
        valid_ratio=args.valid_ratio,
        continue_on_error=args.continue_on_error,
        matrix_cache_dir=args.matrix_cache_dir,
        use_matrix_cache=args.use_matrix_cache,
        use_batch_eval=args.use_batch_eval,
        batch_eval_output_dir=args.batch_eval_output_dir,
        batch_eval_chunk_size=args.batch_eval_chunk_size,
        batch_eval_device=args.batch_eval_device,
        use_eval_cache=args.use_eval_cache,
        eval_cache_dir=args.eval_cache_dir,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        min_listing_days=args.min_listing_days,
        exclude_st=args.exclude_st,
        run_leakage_audit=args.run_leakage_audit,
        leakage_audit_dir=args.leakage_audit_dir,
        fail_on_leakage_blocker=args.fail_on_leakage_blocker,
        corporate_action_aware=args.corporate_action_aware,
        target_return_mode=args.target_return_mode,
        corporate_action_dir=args.corporate_action_dir,
        corporate_action_cash_field=args.corporate_action_cash_field,
        data_freeze_dir=args.data_freeze_dir,
        data_freeze_id=args.data_freeze_id,
        data_version_manifest_path=args.data_version_manifest_path,
        require_data_freeze=args.require_data_freeze,
        freeze_validation_report_path=args.freeze_validation_report_path,
        compute_state_dir=args.compute_state_dir,
        compute_output_dir=args.compute_output_dir,
        use_compute_scheduler=args.use_compute_scheduler,
        formula_shard_count=args.formula_shard_count,
        formula_shard_id=args.formula_shard_id,
        resource_report_path=args.resource_report_path,
        experiment_id=args.experiment_id,
        alpha_candidates_path=args.alpha_candidates_path if args.use_alpha_shortlist_as_seed else None,
        alpha_seed_top_k=args.alpha_seed_top_k,
        alpha_campaign_manifest_path=args.alpha_campaign_manifest_path,
        feature_set_name=args.feature_set_name,
        feature_set_manifest_path=args.feature_set_manifest_path,
    ).run()
    payload = result.to_dict()
    payload.update(freeze_payload)
    _attach_pit_metadata(payload, args)
    _attach_search_trial_summary(payload)
    _maybe_run_validation_and_certification(payload, args)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _run_neural(args: argparse.Namespace) -> dict[str, object]:
    result = NeuralFormulaTrainer(
        config=_neural_config_from_args(args),
        data_dir=args.data_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        factor_store_dir=args.factor_store_dir,
        report_dir=args.report_dir,
        output_dir=args.output_dir,
        correlation_threshold=args.correlation_threshold,
        min_coverage=args.min_coverage,
    ).train()
    payload = result.to_dict()
    payload["search_mode"] = "neural"
    return payload


def _run_hybrid(args: argparse.Namespace, search_config: FormulaSearchConfig) -> dict[str, object]:
    output_dir = Path(args.output_dir)
    neural_output = output_dir / "neural"
    neural_result = NeuralFormulaTrainer(
        config=_neural_config_from_args(args),
        data_dir=args.data_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        factor_store_dir=args.factor_store_dir,
        report_dir=args.report_dir,
        output_dir=str(neural_output),
        correlation_threshold=args.correlation_threshold,
        min_coverage=args.min_coverage,
    ).train()
    random_result = FormulaSearchRunner(
        search_config=search_config,
        data_dir=args.data_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        factor_store_dir=args.factor_store_dir,
        report_dir=args.report_dir,
        output_dir=args.output_dir,
        factor_transform=args.factor_transform,
        enable_gate=args.enable_gate and not args.disable_gate,
        correlation_threshold=args.correlation_threshold,
        min_coverage=args.min_coverage,
        composite_method=args.composite_method,
        train_ratio=args.train_ratio,
        valid_ratio=args.valid_ratio,
        continue_on_error=args.continue_on_error,
        matrix_cache_dir=args.matrix_cache_dir,
        use_matrix_cache=args.use_matrix_cache,
        use_batch_eval=args.use_batch_eval,
        batch_eval_output_dir=args.batch_eval_output_dir,
        batch_eval_chunk_size=args.batch_eval_chunk_size,
        batch_eval_device=args.batch_eval_device,
        use_eval_cache=args.use_eval_cache,
        eval_cache_dir=args.eval_cache_dir,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        min_listing_days=args.min_listing_days,
        exclude_st=args.exclude_st,
        run_leakage_audit=args.run_leakage_audit,
        leakage_audit_dir=args.leakage_audit_dir,
        fail_on_leakage_blocker=args.fail_on_leakage_blocker,
        corporate_action_aware=args.corporate_action_aware,
        target_return_mode=args.target_return_mode,
        corporate_action_dir=args.corporate_action_dir,
        corporate_action_cash_field=args.corporate_action_cash_field,
        data_freeze_dir=args.data_freeze_dir,
        data_freeze_id=args.data_freeze_id,
        data_version_manifest_path=args.data_version_manifest_path,
        require_data_freeze=args.require_data_freeze,
        freeze_validation_report_path=args.freeze_validation_report_path,
        alpha_candidates_path=args.alpha_candidates_path if args.use_alpha_shortlist_as_seed else None,
        alpha_seed_top_k=args.alpha_seed_top_k,
        alpha_campaign_manifest_path=args.alpha_campaign_manifest_path,
        feature_set_name=args.feature_set_name,
        feature_set_manifest_path=args.feature_set_manifest_path,
    ).run()
    payload = random_result.to_dict()
    neural_payload = neural_result.to_dict()
    payload["search_mode"] = "hybrid"
    payload["neural_metadata"] = {
        "search_id": neural_payload["search_id"],
        "approved_factor_ids": neural_payload["approved_factor_ids"],
        "composite_factor_id": neural_payload["composite_factor_id"],
        "checkpoint_paths": neural_payload["checkpoint_paths"],
        "paths": neural_payload["paths"],
        "hybrid_neural_ratio": args.hybrid_neural_ratio,
    }
    payload["approved_factor_ids"] = _unique(payload.get("approved_factor_ids", []) + neural_payload["approved_factor_ids"])
    if neural_payload.get("composite_factor_id"):
        payload["composite_factor_id"] = neural_payload["composite_factor_id"]
    (output_dir / "search_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _neural_config_from_args(args: argparse.Namespace) -> NeuralSearchConfig:
    return NeuralSearchConfig(
        seed=args.seed,
        max_formula_len=args.max_formula_len,
        warmup_steps=args.neural_warmup_steps,
        policy_steps=args.neural_policy_steps,
        batch_size=max(1, min(args.population_size, 8)),
        samples_per_step=max(1, int(args.population_size * max(0.0, min(args.hybrid_neural_ratio, 1.0)))),
        max_complexity=args.max_complexity,
        max_lookback=args.max_lookback,
        resume_checkpoint=args.neural_checkpoint,
        factor_transform=args.factor_transform,
        enable_gate=args.enable_gate and not args.disable_gate,
        top_k=args.top_k,
        composite_method=args.composite_method,
        corpus_sequence_path=args.corpus_sequence_path or _sequence_path_from_corpus(args.corpus_path),
        matrix_cache_dir=args.matrix_cache_dir,
        use_matrix_cache=args.use_matrix_cache,
        use_batch_eval=args.use_batch_eval,
        batch_eval_output_dir=args.batch_eval_output_dir,
        batch_eval_chunk_size=args.batch_eval_chunk_size,
        batch_eval_device=args.batch_eval_device,
        use_eval_cache=args.use_eval_cache,
        eval_cache_dir=args.eval_cache_dir,
    )


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _sequence_path_from_corpus(corpus_path: str | None) -> str | None:
    if not corpus_path:
        return None
    path = Path(corpus_path)
    sibling = path.parent / "formula_sequences.jsonl"
    return str(sibling if sibling.exists() else path)


def _attach_pit_metadata(payload: dict[str, object], args: argparse.Namespace) -> None:
    payload["point_in_time"] = bool(args.point_in_time)
    payload["feature_cutoff_mode"] = args.feature_cutoff_mode
    payload["min_listing_days"] = int(args.min_listing_days)
    payload["exclude_st"] = bool(args.exclude_st)
    payload["leakage_audit_requested"] = bool(args.run_leakage_audit)
    payload["corporate_action_aware"] = bool(args.corporate_action_aware)
    payload["target_return_mode"] = args.target_return_mode
    if args.corporate_action_dir:
        payload["corporate_action_dir"] = args.corporate_action_dir
    if args.leakage_audit_dir:
        payload["leakage_audit_dir"] = args.leakage_audit_dir
    if args.data_freeze_dir:
        payload["data_freeze_dir"] = args.data_freeze_dir
    if args.data_freeze_id:
        payload["data_freeze_id"] = args.data_freeze_id
    if args.data_version_manifest_path:
        payload["data_version_manifest_path"] = args.data_version_manifest_path


def _maybe_run_validation_and_certification(payload: dict[str, object], args: argparse.Namespace) -> None:
    if not (args.run_validation_lab or args.run_certification):
        payload["total_search_trials"] = int(payload.get("candidates_generated", 0) or 0)
        payload["selected_factor_id"] = payload.get("composite_factor_id")
        payload["validation_target_count"] = 1 if payload.get("composite_factor_id") else 0
        return
    factor_id = str(payload.get("composite_factor_id") or _latest_composite(args.factor_store_dir) or "")
    if not factor_id:
        return
    validation_dir = Path(args.validation_output_dir) if args.validation_output_dir else Path(args.output_dir) / "validation_lab"
    certification_dir = Path(args.certification_output_dir) if args.certification_output_dir else Path(args.output_dir) / "factor_certification"
    if args.run_validation_lab:
        validation_argv = [
            "run-suite",
            "--data-dir",
            args.data_dir,
            "--factor-store-dir",
            args.factor_store_dir,
            "--factor-id",
            factor_id,
            "--factor-type",
            "composite",
            "--output-dir",
            str(validation_dir),
            "--run-multiple-testing",
            "--run-overfit-risk",
            "--run-placebo",
            "--run-regime",
            "--run-sensitivity",
            "--run-stress-backtest",
            "--formula-search-result-path",
            str(Path(args.output_dir) / "search_result.json"),
        ]
        if args.data_freeze_dir:
            validation_argv.extend(["--data-freeze-dir", args.data_freeze_dir])
        if args.universe_name:
            validation_argv.extend(["--universe-name", args.universe_name])
        payload["validation_summary"] = _run_child_json(run_validation_main, validation_argv)
        payload["validation_output_dir"] = str(validation_dir)
    if args.run_certification:
        cert_argv = [
            "run",
            "--factor-store-dir",
            args.factor_store_dir,
            "--factor-id",
            factor_id,
            "--factor-type",
            "composite",
            "--output-dir",
            str(certification_dir),
            "--policy-profile",
            args.certification_policy_profile,
            "--validation-lab-report-path",
            str(validation_dir / "validation_lab_report.json"),
            "--factor-validation-summary-path",
            str(validation_dir / "factor_validation_summary.json"),
            "--multiple-testing-report-path",
            str(validation_dir / "multiple_testing_report.json"),
            "--overfit-risk-report-path",
            str(validation_dir / "overfit_risk_report.json"),
            "--placebo-test-report-path",
            str(validation_dir / "placebo_test_report.json"),
            "--regime-validation-report-path",
            str(validation_dir / "regime_validation_report.json"),
            "--sensitivity-report-path",
            str(validation_dir / "sensitivity_report.json"),
            "--stress-backtest-report-path",
            str(validation_dir / "stress_backtest_report.json"),
        ]
        if args.certification_policy_path:
            cert_argv.extend(["--policy-path", args.certification_policy_path])
        payload["certification_summary"] = _run_child_json(run_certify_main, cert_argv)
        payload["certification_output_dir"] = str(certification_dir)
    payload["selected_factor_id"] = factor_id
    payload["validation_target_count"] = 1


def _attach_search_trial_summary(payload: dict[str, object]) -> None:
    candidates_generated = int(payload.get("candidates_generated", 0) or 0)
    candidates_valid = int(payload.get("candidates_valid", 0) or 0)
    candidates_evaluated = int(payload.get("candidates_evaluated", 0) or 0)
    hashes = set()
    for item in payload.get("best_candidates", []) if isinstance(payload.get("best_candidates"), list) else []:
        if isinstance(item, dict) and item.get("formula_hash"):
            hashes.add(str(item["formula_hash"]))
    payload.setdefault("alpha_seed_count", 0)
    payload["total_search_trials"] = candidates_generated
    payload["valid_search_trials"] = candidates_valid
    payload["evaluated_search_trials"] = candidates_evaluated
    payload["unique_formula_hash_count"] = max(len(hashes), candidates_valid if candidates_valid else 0)
    payload["selected_factor_id"] = payload.get("composite_factor_id")
    payload["validation_target_count"] = 1 if payload.get("composite_factor_id") else 0


def _latest_composite(factor_store_dir: str) -> str | None:
    record = LocalFactorStore(factor_store_dir).load_latest_factor(status="approved", factor_type="composite")
    return record.factor_id if record else None


def _run_child_json(main_func, argv: list[str]) -> dict[str, object]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = main_func(argv)
    if exit_code != 0:
        raise RuntimeError(f"child command failed: {argv}")
    output = buffer.getvalue().strip()
    return json.loads(output) if output else {}


def _apply_data_freeze_args(args: argparse.Namespace) -> dict[str, object]:
    report = validate_research_input(
        data_dir=args.data_dir,
        data_freeze_dir=args.data_freeze_dir,
        require_freeze=args.require_data_freeze,
    )
    if report.error_count > 0:
        raise RuntimeError(f"data freeze validation failed: {report.status}")
    if args.data_freeze_dir:
        args.data_dir = str(Path(args.data_freeze_dir) / "data")
    return {
        "data_freeze_id": args.data_freeze_id or report.freeze_id,
        "data_freeze_hash": report.content_hash,
        "freeze_validation_status": report.status,
        "freeze_validation_report_path": args.freeze_validation_report_path,
        "data_version_manifest_path": args.data_version_manifest_path,
    }


if __name__ == "__main__":
    raise SystemExit(main())
