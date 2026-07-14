"""CLI for local out-of-sample validation and anti-overfit diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact
from backtest.io import describe_factor, select_factor_id
from data_lake import validate_research_input
from evaluation import evaluate_by_splits, split_trade_dates
from factor_store import LocalFactorStore
from alpha_experiment_store.leaderboard import load_candidate_pool
from feature_promotion import load_promotion_gate
from model_core.data_loader import AShareDataLoader
from model_core.backtest import AShareFactorEvaluator

from .materialization import FactorMaterializer, MaterializationInputs, load_materialized_factor
from .metrics import evaluate_factor_splits
from .models import (
    FactorValidationTarget,
    MultipleTestingSummary,
    OverfitRiskSummary,
    PlaceboTestResult,
    ValidationIssue,
    ValidationLabReport,
    ValidationSeverity,
)
from .multiple_testing import analyze_multiple_testing
from .overfit import estimate_overfit_risk
from .placebo import run_placebo_tests
from .policy import load_validation_policy
from .regime import run_regime_validation
from .report import write_validation_lab_artifacts
from .sensitivity import run_sensitivity_tests
from .splits import build_splits
from .stress_backtest import run_stress_backtest_bundle


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run factor validation lab diagnostics.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in [
        "validate-factor",
        "validate-candidates",
        "multiple-testing",
        "overfit-risk",
        "placebo",
        "regime",
        "sensitivity",
        "stress-backtest",
        "run-suite",
        "report",
        "smoke",
    ]:
        cmd = sub.add_parser(name)
        _add_common_args(cmd)
    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir")
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--real-data-profile-path")
    parser.add_argument("--require-real-data-freeze", action="store_true")
    parser.add_argument("--real-data-sla-report-path")
    parser.add_argument("--require-real-data-sla-pass", action="store_true")
    parser.add_argument("--matrix-refresh-report-path")
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--feature-tensor-path")
    parser.add_argument("--feature-validity-tensor-path")
    parser.add_argument("--snapshot-proof-manifest-path")
    parser.add_argument("--campaign-manifest-path")
    parser.add_argument("--materialization-dir")
    parser.add_argument("--materialization-manifest-path")
    parser.add_argument("--strict-materialization", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--factor-id")
    parser.add_argument("--factor-type", choices=["single", "composite", "any"], default="any")
    parser.add_argument("--latest-approved", action="store_true")
    parser.add_argument("--latest-production-candidate", action="store_true")
    parser.add_argument("--validation-candidate-pool-path")
    parser.add_argument("--max-candidates", type=int, default=0)
    parser.add_argument("--candidate-rank-range")
    parser.add_argument("--family-filter")
    parser.add_argument("--alpha-factory-report-path")
    parser.add_argument("--alpha-candidates-path")
    parser.add_argument("--alpha-shortlist-path")
    parser.add_argument("--alpha-full-eval-summary-path")
    parser.add_argument("--formula-search-result-path")
    parser.add_argument("--batch-eval-result-path")
    parser.add_argument("--feature-set-manifest-path")
    parser.add_argument("--feature-promotion-policy-path")
    parser.add_argument("--feature-promotion-allowlist-path")
    parser.add_argument("--feature-promotion-denylist-path")
    parser.add_argument("--require-feature-promotion", action="store_true")
    parser.add_argument("--allow-risk-filter-features", action="store_true")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--as-of-date", default="20240104")
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--split-method", default="rolling_walk_forward")
    parser.add_argument("--validation-policy", default="real_long_history_engineering_robustness_v2")
    parser.add_argument("--train-size", type=int, default=756)
    parser.add_argument("--validation-size", type=int, default=126)
    parser.add_argument("--test-size", type=int, default=126)
    parser.add_argument("--step-size", type=int, default=126)
    parser.add_argument("--embargo-size", type=int, default=0)
    parser.add_argument("--label-horizon", type=int, default=1)
    parser.add_argument("--research-end-date")
    parser.add_argument("--holdout-start-date")
    parser.add_argument("--cscv-groups", type=int, default=2)
    parser.add_argument("--max-cscv-combinations", type=int, default=6)
    parser.add_argument("--run-multiple-testing", action="store_true")
    parser.add_argument("--run-overfit-risk", action="store_true")
    parser.add_argument("--run-placebo", action="store_true")
    parser.add_argument("--placebo-trials", type=int, default=12)
    parser.add_argument("--run-regime", action="store_true")
    parser.add_argument("--run-sensitivity", action="store_true")
    parser.add_argument("--run-stress-backtest", action="store_true")
    parser.add_argument("--cost-multipliers", default="1.0,2.0")
    parser.add_argument("--capacity-participations", default="0.10,0.05")
    parser.add_argument("--top-n-values", default="2")
    parser.add_argument("--max-weight-values", default="0.10")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--fail-on-blocker", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    if args.fail_on_blocker and int(payload.get("validation_blocker_count", 0) or 0) > 0:
        return 1
    return 0


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.validation_candidate_pool_path:
        return _run_candidate_pool(args)
    return _run_single(args)


def _run_candidate_pool(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    if str(args.device).startswith("cuda") and torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    rows = _filter_candidate_pool(load_candidate_pool(args.validation_candidate_pool_path), args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        candidate_dir = output_dir / "candidates" / f"rank_{int(row.get('rank', idx + 1)):04d}_{row.get('factor_id', 'factor')}"
        try:
            result = _run_single(
                args,
                factor_id_override=str(row.get("factor_id")),
                output_dir_override=str(candidate_dir),
                factor_store_dir_override=str(row.get("factor_store_dir") or args.factor_store_dir),
                candidate_pool_row=row,
            )
        except Exception as exc:
            factor_id = str(row.get("factor_id"))
            materialization_manifest = Path(args.materialization_dir or output_dir / "materialized_factors") / factor_id / "materialization_manifest.json"
            result = {
                "status": "blocked",
                "factor_id": factor_id,
                "validation_blocker_count": 1,
                "validation_summary": {"status": "blocked", "blocker_count": 1, "warning_count": 0, "out_of_sample_score": 0.0, "metrics": {}},
                "error": str(exc),
                "paths": {"materialization_manifest_path": str(materialization_manifest) if materialization_manifest.exists() else ""},
            }
        results.append(
            {
                "factor_id": result.get("factor_id"),
                "rank": row.get("rank"),
                "status": result.get("status"),
                "validation_blocker_count": result.get("validation_blocker_count", 0),
                "out_of_sample_score": (result.get("validation_summary") or {}).get("out_of_sample_score", 0.0),
                "validation_summary": result.get("validation_summary", {}),
                "selection_data_reused": True,
                "untouched_holdout": False,
                "evidence_level": "retrospective_engineering_only",
                "certification_ready": False,
                "portfolio_ready": False,
                "error": result.get("error"),
                "source_candidate": row,
                "paths": result.get("paths", {}),
            }
        )
    results_path = write_jsonl_artifact(
        output_dir / "validation_candidate_pool_results.jsonl",
        results,
        "validation_candidate_pool_results",
        "validation_lab",
    )
    passed = sum(1 for item in results if item.get("status") == "passed")
    blocked = sum(1 for item in results if item.get("status") == "blocked")
    report = {
        "status": "passed" if blocked == 0 else "blocked",
        "validation_candidate_pool_path": args.validation_candidate_pool_path,
        "candidate_count": len(rows),
        "validated_candidate_count": len(results),
        "passed_count": passed,
        "blocked_count": blocked,
        "results_path": str(results_path),
        "top_results": results[:10],
        "resource_usage": {
            "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES", ""),
            "requested_device": args.device,
            "actual_device": str(torch.cuda.current_device()) if str(args.device).startswith("cuda") and torch.cuda.is_available() else "cpu",
            "peak_gpu_memory_mb": float(torch.cuda.max_memory_allocated() / 1024**2) if str(args.device).startswith("cuda") and torch.cuda.is_available() else 0.0,
            "elapsed_seconds": float(time.perf_counter() - started),
            "throughput_factors_per_second": float(len(results) / max(time.perf_counter() - started, 1e-9)),
            "fallback_to_cpu": bool(str(args.device).startswith("cuda") and not torch.cuda.is_available()),
        },
    }
    report_path = write_json_artifact(output_dir / "validation_candidate_pool_report.json", report, "validation_candidate_pool_report", "validation_lab")
    return report | {
        "paths": {
            "validation_candidate_pool_report_path": str(report_path),
            "validation_candidate_pool_results_path": str(results_path),
        },
        "validation_blocker_count": blocked,
    }


def _run_single(
    args: argparse.Namespace,
    *,
    factor_id_override: str | None = None,
    output_dir_override: str | None = None,
    factor_store_dir_override: str | None = None,
    candidate_pool_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    freeze_report = validate_research_input(args.data_dir, args.data_freeze_dir, args.require_data_freeze or args.require_real_data_freeze)
    if freeze_report.error_count > 0 and (args.require_data_freeze or args.require_real_data_freeze):
        raise RuntimeError("data freeze validation failed")
    if args.require_real_data_sla_pass and not _real_data_sla_passed(args.real_data_sla_report_path):
        raise RuntimeError("real data SLA did not pass")
    data_dir = str(Path(args.data_freeze_dir) / "data") if args.data_freeze_dir else args.data_dir
    if not data_dir:
        raise ValueError("--data-dir or --data-freeze-dir is required")
    store = LocalFactorStore(factor_store_dir_override or args.factor_store_dir)
    factor_id = factor_id_override or _select_factor(args, store)
    factor_meta = describe_factor(store, factor_id)
    factor_record = next((record for record in store.load_factors() if record.factor_id == factor_id), None)
    if factor_record is None:
        raise RuntimeError(f"factor metadata missing: {factor_id}")
    policy = load_validation_policy(args.validation_policy)
    if args.strict_materialization:
        matrix = _load_governed_matrix_context(args)
        manifest_path = args.materialization_manifest_path
        if not manifest_path:
            if not args.materialization_dir or not args.feature_tensor_path or not args.feature_set_manifest_path or not args.matrix_cache_dir or not args.data_freeze_dir:
                raise RuntimeError("strict materialization requires freeze, matrix, feature manifest/tensor and materialization dir")
            materializer = FactorMaterializer(
                MaterializationInputs(
                    data_freeze_dir=args.data_freeze_dir,
                    matrix_cache_dir=args.matrix_cache_dir,
                    feature_manifest_path=args.feature_set_manifest_path,
                    feature_tensor_path=args.feature_tensor_path,
                    promotion_policy_path=args.feature_promotion_policy_path,
                    target_return_mode=matrix["target_return_mode"],
                    feature_cutoff_mode=matrix["feature_cutoff_mode"],
                    point_in_time=True,
                    campaign_manifest_path=args.campaign_manifest_path,
                    feature_validity_tensor_path=args.feature_validity_tensor_path,
                    snapshot_proof_manifest_path=args.snapshot_proof_manifest_path,
                    promotion_allowlist_path=args.feature_promotion_allowlist_path,
                    promotion_denylist_path=args.feature_promotion_denylist_path,
                ),
                args.materialization_dir,
                device=args.device,
                min_coverage=policy.min_coverage,
                max_coverage=policy.max_coverage,
            )
            materialization = materializer.materialize(factor_record)
            if materialization.status != "success":
                raise RuntimeError(f"factor materialization blocked: {materialization.blocker}")
            manifest_path = materialization.manifest_path
        factors, validity, materialization_manifest = load_materialized_factor(manifest_path)
        if tuple(factors.shape) != tuple(matrix["target_ret"].shape):
            raise RuntimeError("materialized factor axis/shape mismatch")
        trade_dates = matrix["trade_dates"]
        target_ret = matrix["target_ret"]
        raw_data_cache = matrix["raw_data_cache"]
    else:
        loader = AShareDataLoader(
            data_dir=data_dir,
            device="cpu",
            universe_name=args.universe_name,
            universe_file=args.universe_file,
            research_end_date=args.research_end_date,
            holdout_start_date=args.holdout_start_date,
            label_horizon=args.label_horizon,
        ).load_data()
        factors = store.load_factor_values_matrix(factor_id, loader.ts_codes, loader.trade_dates, device="cpu")
        validity = torch.isfinite(factors)
        materialization_manifest = {}
        trade_dates = loader.trade_dates
        target_ret = loader.target_ret
        raw_data_cache = loader.raw_data_cache
        matrix = {
            "active_mask": raw_data_cache.get("active_mask"),
            "pit_available_mask": raw_data_cache.get("pit_available_mask"),
            "index_member_matrix": raw_data_cache.get("index_member_matrix"),
            "firewall_proof": loader.date_firewall.proof(loader.trade_dates, raw_truncated_before_compute=True) if loader.date_firewall else {},
        }
    effective_embargo = max(int(args.embargo_size), int(factor_record.lookback_days or 1) + int(args.label_horizon))
    splits = build_splits(
        args.split_method,
        trade_dates,
        args.train_size,
        args.validation_size,
        args.test_size,
        args.step_size,
        effective_embargo,
        args.cscv_groups,
        args.max_cscv_combinations,
    )
    window_results, validation_summary, issues = evaluate_factor_splits(
        factors,
        target_ret,
        trade_dates,
        splits,
        factor_id,
        validity=validity,
        active_mask=matrix.get("active_mask"),
        target_available_mask=matrix.get("pit_available_mask"),
        index_member_mask=matrix.get("index_member_matrix"),
        policy=policy,
    )
    screening_reproduction = _screening_reproduction(factor_record, factors, target_ret, trade_dates)
    if screening_reproduction.get("available") and not screening_reproduction.get("within_tolerance"):
        issues.append(ValidationIssue("blocker", "screening_reproduction_mismatch", "materialized factor did not reproduce original full-eval metrics", screening_reproduction))
        validation_summary = replace(validation_summary, blocker_count=validation_summary.blocker_count + 1, status="statistically_rejected")
    promotion_metadata, promotion_issues = _feature_promotion_metadata(
        args.feature_set_manifest_path,
        list(_factor_field(store, factor_id, "formula") or []),
        args,
    )
    issues.extend(promotion_issues)
    if promotion_issues:
        validation_summary = replace(
            validation_summary,
            blocker_count=validation_summary.blocker_count + sum(item.severity == "blocker" for item in promotion_issues),
            warning_count=validation_summary.warning_count + sum(item.severity == "warning" for item in promotion_issues),
            status="statistically_rejected",
        )
    firewall_proof = dict(matrix.get("firewall_proof") or {})
    if args.research_end_date and not firewall_proof.get("research_holdout_firewall_enabled"):
        issues.append(ValidationIssue("blocker", "research_firewall_proof_failed", "research cutoff was configured without a proven pre-compute data boundary", firewall_proof))
        validation_summary = replace(
            validation_summary,
            blocker_count=validation_summary.blocker_count + 1,
            status="data_blocked",
        )
    multiple_testing, mt_rows = analyze_multiple_testing(
        factor_store=store,
        alpha_factory_report_path=args.alpha_factory_report_path,
        alpha_candidates_path=args.alpha_candidates_path,
        alpha_shortlist_path=args.alpha_shortlist_path,
        alpha_full_eval_summary_path=args.alpha_full_eval_summary_path,
        formula_search_result_path=args.formula_search_result_path,
        batch_eval_result_path=args.batch_eval_result_path,
    )
    overfit = estimate_overfit_risk(window_results, multiple_testing)
    placebo = None
    placebo_trials: list[dict[str, Any]] = []
    if args.run_placebo or args.command in {"placebo", "run-suite", "smoke"}:
        placebo, placebo_trials = run_placebo_tests(
            factor_id,
            factors,
            target_ret,
            trade_dates,
            validation_summary.out_of_sample_score,
            n_trials=args.placebo_trials,
        )
    regimes = []
    regime_summary: dict[str, Any] = {"regime_count": 0, "regime_pass_ratio": 0.0}
    if args.run_regime or args.command in {"regime", "run-suite", "smoke"}:
        regimes, regime_summary = run_regime_validation(factors, target_ret, trade_dates, raw_data_cache)
    sensitivity_results = []
    sensitivity_surface: dict[str, Any] = {"scenario_count": 0, "sensitivity_pass_ratio": 0.0}
    if args.run_sensitivity or args.command in {"sensitivity", "run-suite", "smoke"}:
        sensitivity_surface = {"scenario_count": 0, "sensitivity_pass_ratio": 0.0, "status": "unsupported", "reason": "requires real A-share simulator reruns"}
    stress_results = []
    stress_summary: dict[str, Any] = {"stress_scenario_count": 0, "stress_backtest_pass_ratio": 0.0}
    if args.run_stress_backtest or args.command in {"stress-backtest", "run-suite", "smoke"}:
        stress_summary = {"stress_scenario_count": 0, "stress_backtest_pass_ratio": 0.0, "status": "unsupported", "reason": "requires real A-share simulator reruns with independent curves/fills/costs"}
    target = FactorValidationTarget(
        factor_id=factor_id,
        factor_type=factor_meta.get("factor_type", args.factor_type),
        formula_hash=_factor_field(store, factor_id, "formula_hash"),
        formula_names=list(_factor_field(store, factor_id, "formula") or []),
        feature_set_name=_feature_name(args.feature_set_manifest_path),
        alpha_campaign_id=_alpha_campaign_id(args.alpha_factory_report_path),
        source_artifacts=_source_artifacts(args),
        metadata={
            "factor_meta": factor_meta,
            "freeze_status": freeze_report.status,
            "candidate_pool_row": candidate_pool_row or {},
            "validation_policy": policy.to_dict(),
            "effective_embargo_size": effective_embargo,
            "label_horizon": args.label_horizon,
            "selection_data_reused": True,
            "untouched_holdout": False,
            "evidence_level": "retrospective_engineering_only",
            "certification_supported": False,
            "portfolio_supported": False,
            "materialization_manifest": materialization_manifest,
            "screening_reproduction": screening_reproduction,
            "research_end_date": args.research_end_date,
            "holdout_start_date": args.holdout_start_date,
            "research_holdout_firewall_enabled": bool(firewall_proof.get("research_holdout_firewall_enabled", False)),
            "research_holdout_firewall_proof": firewall_proof,
            "universe_mode": matrix.get("universe_mode", "unknown"),
            "survivorship_bias_blocker": bool(matrix.get("survivorship_bias_blocker", False)),
            **_feature_pit_metadata(args.feature_set_manifest_path, list(_factor_field(store, factor_id, "formula") or [])),
            **promotion_metadata,
        },
    )
    status = "historical_replay_passed" if validation_summary.status == "engineering_passed" else validation_summary.status
    report = ValidationLabReport(
        created_at=_utc_now(),
        target=target.to_dict(),
        split_method=args.split_method,
        splits=[item.to_dict() for item in splits],
        validation_summary=validation_summary.to_dict(),
        multiple_testing_summary=multiple_testing.to_dict(),
        overfit_risk_summary=overfit.to_dict(),
        placebo_summary=placebo.to_dict() if placebo else {"enabled": False},
        regime_summary=regime_summary,
        sensitivity_summary=sensitivity_surface,
        stress_backtest_summary=stress_summary,
        issues=[item.to_dict() for item in issues],
        status=status,
    )
    paths = write_validation_lab_artifacts(
        output_dir_override or args.output_dir,
        report,
        splits,
        window_results,
        validation_summary,
        multiple_testing,
        overfit,
        placebo,
        [*placebo_trials, *({"multiple_testing_rank": row} for row in mt_rows[:0])],
        regimes,
        sensitivity_results,
        sensitivity_surface,
        stress_results,
        issues,
    )
    return {
        "status": status,
        "factor_id": factor_id,
        "factor_type": factor_meta.get("factor_type", args.factor_type),
        "split_method": args.split_method,
        "split_count": len(splits),
        "validation_blocker_count": validation_summary.blocker_count,
        "validation_warning_count": validation_summary.warning_count,
        "pbo_estimate": overfit.pbo_estimate,
        "deflated_ic_score": overfit.deflated_ic_like_score,
        "placebo_percentile": placebo.candidate_vs_placebo_percentile if placebo else 0.0,
        "regime_pass_ratio": float(regime_summary.get("regime_pass_ratio", 0.0) or 0.0),
        "sensitivity_pass_ratio": float(sensitivity_surface.get("sensitivity_pass_ratio", 0.0) or 0.0),
        "stress_backtest_pass_ratio": float(stress_summary.get("stress_backtest_pass_ratio", 0.0) or 0.0),
        "validation_summary": validation_summary.to_dict(),
        "multiple_testing_summary": multiple_testing.to_dict(),
        "overfit_risk_summary": overfit.to_dict(),
        "selection_data_reused": True,
        "untouched_holdout": False,
        "evidence_level": "retrospective_engineering_only",
        "certification_ready": False,
        "portfolio_ready": False,
        "paths": paths,
    }


def _load_governed_matrix_context(args: argparse.Namespace) -> dict[str, Any]:
    matrix_dir = Path(args.matrix_cache_dir)
    required = ["trade_dates.json", "ts_codes.json", "active_mask.npy", "pit_available_mask.npy", "index_member_matrix.npy", "matrix_version_manifest.json"]
    missing = [name for name in required if not (matrix_dir / name).exists()]
    if missing:
        raise RuntimeError(f"missing governed matrix artifacts: {','.join(missing)}")
    trade_dates = [str(item) for item in json.loads((matrix_dir / "trade_dates.json").read_text(encoding="utf-8"))]
    manifest = json.loads((matrix_dir / "matrix_version_manifest.json").read_text(encoding="utf-8"))
    target_mode = str(manifest.get("target_return_mode") or "adjusted_close")
    price_name = "total_return.npy" if target_mode == "corporate_action_total_return" else "adjusted_close.npy"
    price = np.asarray(np.load(matrix_dir / price_name, mmap_mode="r"), dtype=np.float32)
    target = np.zeros_like(price, dtype=np.float32)
    target_valid = np.zeros_like(price, dtype=bool)
    valid_price = np.isfinite(price[:, :-1]) & np.isfinite(price[:, 1:]) & (np.abs(price[:, :-1]) > 1e-12)
    next_return = np.zeros_like(price[:, :-1], dtype=np.float32)
    next_return[valid_price] = np.log(np.clip(price[:, 1:][valid_price], 1e-6, None) / np.clip(price[:, :-1][valid_price], 1e-6, None))
    target[:, :-1] = next_return
    target_valid[:, :-1] = valid_price
    masks = {name: torch.tensor(np.asarray(np.load(matrix_dir / f"{name}.npy", mmap_mode="r"), dtype=np.float32), dtype=torch.bool) for name in ["active_mask", "pit_available_mask", "index_member_matrix"]}
    index_np = masks["index_member_matrix"].numpy()
    has_pit_constituent_proof = bool(
        manifest.get("universe_mode") == "daily_pit_constituents"
        and manifest.get("historical_constituent_proof")
    )
    fixed_asof = not has_pit_constituent_proof
    target_tensor = torch.from_numpy(target)
    return {
        "trade_dates": trade_dates,
        "target_ret": target_tensor,
        "active_mask": masks["active_mask"],
        "pit_available_mask": masks["pit_available_mask"] & torch.from_numpy(target_valid),
        "index_member_matrix": masks["index_member_matrix"],
        "target_return_mode": target_mode,
        "feature_cutoff_mode": str(manifest.get("feature_cutoff_mode") or "unknown"),
        "raw_data_cache": masks | {"target_ret": target_tensor},
        "universe_mode": "fixed_asof_constituents" if fixed_asof else "daily_pit_constituents",
        "survivorship_bias_blocker": fixed_asof,
        "firewall_proof": {
            "research_holdout_firewall_enabled": bool(manifest.get("research_holdout_firewall_enabled", False)),
            "research_end_date": manifest.get("research_end_date"),
            "holdout_start_date": manifest.get("holdout_start_date"),
            "label_horizon": manifest.get("label_horizon"),
            "eligible_date_hash": manifest.get("eligible_date_hash"),
            "raw_truncated_before_compute": bool(manifest.get("raw_truncated_before_compute", False)),
            "out_of_bounds_access_count": int(manifest.get("firewall_out_of_bounds_access_count", 0) or 0),
        },
    }


def _screening_reproduction(factor_record, factors: torch.Tensor, target_ret: torch.Tensor, trade_dates: list[str]) -> dict[str, Any]:
    expected = ((factor_record.metadata or {}).get("metrics_by_split") or {}) if factor_record is not None else {}
    if not expected:
        return {"available": False, "within_tolerance": False, "reason": "original metrics unavailable"}
    recomputed = evaluate_by_splits(
        AShareFactorEvaluator(),
        factors,
        {},
        torch.nan_to_num(target_ret, nan=0.0, posinf=0.0, neginf=0.0),
        trade_dates,
        split_trade_dates(trade_dates),
    )
    tolerances = {
        "rank_ic_mean": 1e-3,
        "rank_ic_ir": 3e-3,
        "turnover": 1e-3,
        "top_bottom_spread": 8e-3,
        "score": 1.2e-2,
    }
    comparisons = {}
    within = True
    for split_name in ["train", "valid", "test", "all"]:
        for metric in ["rank_ic_mean", "rank_ic_ir", "top_bottom_spread", "turnover", "score"]:
            if metric not in (expected.get(split_name) or {}):
                continue
            left = float((expected.get(split_name) or {}).get(metric, 0.0))
            right = float((recomputed.get(split_name) or {}).get(metric, 0.0))
            delta = abs(left - right)
            tolerance = tolerances[metric]
            comparisons[f"{split_name}.{metric}"] = {"expected": left, "recomputed": right, "absolute_error": delta, "tolerance": tolerance}
            within = within and delta <= tolerance
    return {"available": True, "within_tolerance": within, "tolerances": tolerances, "comparisons": comparisons}


def _filter_candidate_pool(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    selected = sorted(rows, key=lambda row: int(row.get("rank", 10**9) or 10**9))
    if args.candidate_rank_range:
        start, end = _parse_rank_range(args.candidate_rank_range)
        selected = [row for row in selected if start <= int(row.get("rank", 10**9) or 10**9) <= end]
    if args.family_filter:
        allowed = {item.strip() for item in args.family_filter.split(",") if item.strip()}
        selected = [row for row in selected if str(row.get("family", "")) in allowed]
    if args.max_candidates and args.max_candidates > 0:
        selected = selected[: args.max_candidates]
    return selected


def _parse_rank_range(value: str) -> tuple[int, int]:
    if "-" in value:
        left, right = value.split("-", 1)
        return int(left), int(right)
    rank = int(value)
    return rank, rank


def _select_factor(args: argparse.Namespace, store: LocalFactorStore) -> str:
    if args.latest_production_candidate:
        record = store.load_latest_factor(status="production_candidate", factor_type=None if args.factor_type == "any" else args.factor_type)
        if record is not None:
            return record.factor_id
    return select_factor_id(
        store,
        factor_id=args.factor_id,
        latest_approved=args.latest_approved or not args.factor_id,
        factor_type=args.factor_type,
    )


def _factor_field(store: LocalFactorStore, factor_id: str, field: str) -> Any:
    for record in store.load_factors():
        if record.factor_id == factor_id:
            return getattr(record, field)
    return None


def _feature_name(path: str | None) -> str | None:
    payload = _read_json(path)
    return payload.get("feature_set_name") if payload else None


def _feature_pit_metadata(path: str | None, formula_names: list[str]) -> dict[str, Any]:
    payload = _read_json(path)
    if not payload:
        return {}
    features = {
        str(item.get("feature_name")): dict(item)
        for item in payload.get("feature_definitions", [])
        if isinstance(item, dict) and item.get("feature_name")
    }
    used = [features[name] for name in formula_names if name in features]
    weak = [item.get("feature_name") for item in used if item.get("pit_safety") != "pit_safe"]
    families = sorted({str(item.get("family")) for item in used if item.get("family")})
    return {
        "feature_set_name": payload.get("feature_set_name"),
        "feature_set_hash": payload.get("content_hash"),
        "feature_family_tags": families,
        "weak_pit_feature_used": bool(weak),
        "weak_pit_features": weak,
        "feature_pit_alignment_status": "warning" if weak else "ok",
    }


def _feature_promotion_metadata(path: str | None, formula_names: list[str], args: argparse.Namespace) -> tuple[dict[str, Any], list[ValidationIssue]]:
    payload = _read_json(path)
    if not payload:
        return {}, []
    feature_meta = {
        str(item.get("feature_name")): dict(item)
        for item in payload.get("feature_definitions", [])
        if isinstance(item, dict) and item.get("feature_name")
    }
    gate = load_promotion_gate(
        policy_path=args.feature_promotion_policy_path,
        allowlist_path=args.feature_promotion_allowlist_path,
        denylist_path=args.feature_promotion_denylist_path,
        require_promotion=args.require_feature_promotion,
        allow_risk_filter_features=args.allow_risk_filter_features,
    )
    metadata = {
        "feature_promotion_policy_hash": gate.policy_hash if gate else None,
        "unapproved_feature_used": False,
        "weak_pit_promoted_feature_used": False,
        "risk_filter_feature_used_as_alpha": False,
    }
    issues: list[ValidationIssue] = []
    if gate is None:
        return metadata, issues
    errors, warnings, gate_metadata = gate.check_formula_names(formula_names, feature_meta)
    metadata.update(gate_metadata)
    for error in errors:
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.blocker,
                code="feature_promotion_blocker",
                message=error,
                metadata={"feature_promotion_policy_hash": gate.policy_hash},
            )
        )
    for warning in warnings:
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.warning,
                code="feature_promotion_warning",
                message=warning,
                metadata={"feature_promotion_policy_hash": gate.policy_hash},
            )
        )
    return metadata, issues


def _alpha_campaign_id(path: str | None) -> str | None:
    payload = _read_json(path)
    return payload.get("campaign_id") if payload else None


def _source_artifacts(args: argparse.Namespace) -> dict[str, str]:
    names = [
        "alpha_factory_report_path",
        "alpha_candidates_path",
        "alpha_shortlist_path",
        "alpha_full_eval_summary_path",
        "formula_search_result_path",
        "batch_eval_result_path",
        "feature_set_manifest_path",
        "data_version_manifest_path",
    ]
    return {name: str(getattr(args, name)) for name in names if getattr(args, name, None)}


def _parse_floats(value: str | None) -> list[float]:
    return [float(item.strip()) for item in (value or "").split(",") if item.strip()]


def _parse_ints(value: str | None) -> list[int]:
    return [int(float(item.strip())) for item in (value or "").split(",") if item.strip()]


def _read_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def _real_data_sla_passed(path: str | None) -> bool:
    payload = _read_json(path)
    return str(payload.get("status") or "") in {"pass", "warning"}


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


if __name__ == "__main__":
    raise SystemExit(main())
