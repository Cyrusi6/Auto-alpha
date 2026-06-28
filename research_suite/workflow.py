"""One-click research suite workflow."""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backtest import run_backtest
from data_pipeline import run_pipeline
from factor_store import LocalFactorStore
from factor_lifecycle.run_lifecycle import main as run_lifecycle_main
from formula_batch_eval.run_batch_eval import main as run_formula_batch_eval_main
from formula_corpus.run_corpus import main as run_formula_corpus_main
from formula_search import run_search
from matrix_store.run_build_matrix import main as run_build_matrix_main
from model_core.data_loader import AShareDataLoader
from model_registry import LocalModelRegistry, ModelKind, write_model_registry_report
from neural_search.run_pretrain import main as run_pretrain_main
from performance_benchmark.run_benchmark import main as run_benchmark_main
from strategy_manager import runner as strategy_runner
from universe import run_universe
from leakage_audit.run_audit import main as run_leakage_audit_main
from point_in_time.run_pit import main as run_pit_main

from .catalog import register_artifact, write_artifact_catalog
from .models import ArtifactCatalog, PromotionConfig, ResearchSuiteConfig, ResearchSuiteResult, SuiteStageResult
from .promotion import promote_factor_if_eligible
from .report import write_promotion_decision, write_suite_report
from .walk_forward import build_walk_forward_windows, evaluate_factor_walk_forward


class ResearchSuiteRunner:
    def __init__(self, config: ResearchSuiteConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.catalog = ArtifactCatalog(suite_name=config.suite_name, created_at=_utc_now())
        self.stages: list[SuiteStageResult] = []
        self.selected_factor_id: str | None = None
        self.promotion_decision = None
        self.backtest_summary: dict[str, Any] = {}
        self.model_version_id: str | None = None
        self.model_lifecycle_summary: dict[str, Any] = {}
        self.pit_summary: dict[str, Any] = {}
        self.leakage_summary: dict[str, Any] = {}

    def run(self) -> ResearchSuiteResult:
        started_at = _utc_now()
        status = "success"
        try:
            if not self.config.skip_data_sync:
                self._append_stage("data_sync", self._stage_data_sync)
            if not self.config.skip_universe:
                self._append_stage("universe", self._stage_universe)
            if self.config.run_pit_validation:
                self._append_stage("pit_validation", self._stage_pit_validation)
            if self.config.build_matrix_cache:
                self._append_stage("matrix_cache", self._stage_matrix_cache)
            if self.config.benchmark:
                self._append_stage("benchmark", self._stage_benchmark)
            if self.config.build_formula_corpus:
                self._append_stage("formula_corpus", self._stage_formula_corpus)
            if self.config.pretrain_alphagpt:
                self._append_stage("alphagpt_pretrain", self._stage_alphagpt_pretrain)
            if self.config.use_batch_eval:
                self._append_stage("formula_batch_eval", self._stage_formula_batch_eval)
            self._append_stage("formula_search", self._stage_formula_search)
            self._append_stage("backtest", self._stage_backtest)
            if self.config.run_leakage_audit:
                self._append_stage("leakage_audit", self._stage_leakage_audit)
            if not self.config.skip_orders:
                self._append_stage("orders", self._stage_orders)
            self._append_stage("walk_forward", self._stage_walk_forward)
            if not self.config.disable_promotion and self.config.promote_latest_composite:
                self._append_stage("promotion", self._stage_promotion)
            if self.config.register_model_version:
                self._append_stage("model_registry", self._stage_model_registry)
            if self.config.create_model_review_package or self.config.require_model_approval:
                self._append_stage("model_lifecycle", self._stage_model_lifecycle)
        except Exception:
            status = "failed"

        finished_at = _utc_now()
        result = ResearchSuiteResult(
            suite_name=self.config.suite_name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            stages=self.stages,
            selected_factor_id=self.selected_factor_id,
            promotion_decision=self.promotion_decision,
            paths={
                "suite_result_path": str(self.output_dir / "suite_result.json"),
                "suite_report_path": str(self.output_dir / "suite_report.md"),
                "artifact_catalog_path": str(self.output_dir / "artifact_catalog.json"),
                "artifact_catalog_md_path": str(self.output_dir / "artifact_catalog.md"),
                "promotion_decision_path": str(self.output_dir / "promotion_decision.json"),
            },
            summary={
                "suite_name": self.config.suite_name,
                "status": status,
                "selected_factor_id": self.selected_factor_id,
                "backtest_metrics": self.backtest_summary.get("metrics", {}),
                "matrix_cache_dir": str(self._matrix_cache_dir()),
                "model_version_id": self.model_version_id,
                "model_lifecycle_status": self.model_lifecycle_summary.get("current_status") or self.model_lifecycle_summary.get("lifecycle_status"),
                "model_registry_dir": str(self._model_registry_dir()),
                "model_review_package_path": self.model_lifecycle_summary.get("model_review_package_path"),
                "model_lifecycle_report_path": self.model_lifecycle_summary.get("factor_lifecycle_report_path"),
                "model_approval_id": self.model_lifecycle_summary.get("approval_id"),
                "model_recommended_action": self.model_lifecycle_summary.get("recommended_action"),
                "point_in_time_enabled": self.config.point_in_time,
                "pit_blocker_count": self.pit_summary.get("blocker_count", 0),
                "pit_warning_count": self.pit_summary.get("warning_count", 0),
                "leakage_blocker_count": self.leakage_summary.get("blocker_count", 0),
                "leakage_warning_count": self.leakage_summary.get("warning_count", 0),
                "truncation_consistency_passed": (self.leakage_summary.get("truncation_consistency") or {}).get("passed"),
                "survivorship_warning_count": (self.pit_summary.get("survivorship") or {}).get("warning_count", 0),
                "active_universe_coverage": self.pit_summary.get("active_universe_coverage", 0.0),
            },
        )
        suite_json, suite_md = write_suite_report(result, self.output_dir)
        promotion_path = write_promotion_decision(self.promotion_decision, self.output_dir)
        self.catalog = register_artifact(self.catalog, "suite_result", suite_json, "json", "suite")
        self.catalog = register_artifact(self.catalog, "suite_report", suite_md, "markdown", "suite")
        self.catalog = register_artifact(self.catalog, "promotion_decision", promotion_path, "json", "promotion")
        catalog_json, catalog_md = write_artifact_catalog(self.catalog, self.output_dir)
        self.catalog = register_artifact(self.catalog, "artifact_catalog", catalog_json, "json", "suite")
        self.catalog = register_artifact(self.catalog, "artifact_catalog_markdown", catalog_md, "markdown", "suite")
        write_artifact_catalog(self.catalog, self.output_dir)
        return result

    def _append_stage(self, name: str, func: Callable[[], tuple[dict[str, Any], dict[str, str]]]) -> None:
        started_at = _utc_now()
        try:
            summary, output_paths = func()
            stage = SuiteStageResult(
                name=name,
                status="success",
                started_at=started_at,
                finished_at=_utc_now(),
                output_paths=output_paths,
                summary=summary,
            )
            self.stages.append(stage)
        except Exception as exc:
            stage = SuiteStageResult(
                name=name,
                status="failed",
                started_at=started_at,
                finished_at=_utc_now(),
                error=str(exc),
            )
            self.stages.append(stage)
            raise

    def _stage_data_sync(self) -> tuple[dict[str, Any], dict[str, str]]:
        payload = _run_json_main(
            run_pipeline.main,
            [
                "--sync",
                "--provider",
                self.config.provider,
                "--data-dir",
                self.config.data_dir,
                "--validate",
                "--mode",
                "overwrite",
                "--index-codes",
                self.config.index_code,
            ],
        )
        output_paths = {
            "manifest": str(Path(self.config.data_dir) / "manifest.json"),
            "quality_report": str(Path(self.config.data_dir) / "quality_report.json"),
            "pipeline_state": str(Path(self.config.data_dir) / "pipeline_state.json"),
        }
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, "json", "data_sync")
        return payload, output_paths

    def _stage_universe(self) -> tuple[dict[str, Any], dict[str, str]]:
        argv = [
                "--data-dir",
                self.config.data_dir,
                "--as-of-date",
                self.config.as_of_date,
                "--universe-name",
                self.config.universe_name,
                "--use-index-members",
                "--index-code",
                self.config.index_code,
                "--min-listed-days",
                "0",
                "--min-amount",
                "0",
            ]
        if self.config.point_in_time:
            argv.extend(["--point-in-time", "--min-listing-days", str(self.config.min_listing_days)])
            if self.config.exclude_st:
                argv.append("--exclude-st")
        payload = _run_json_main(run_universe.main, argv)
        output_paths = {
            "universe": str(payload.get("output_path", "")),
            "universe_summary": str(payload.get("summary_path", "")),
        }
        if payload.get("pit_summary_path"):
            output_paths["universe_pit_summary"] = str(payload.get("pit_summary_path"))
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, "jsonl" if name == "universe" else "json", "universe")
        return payload, output_paths

    def _stage_pit_validation(self) -> tuple[dict[str, Any], dict[str, str]]:
        pit_dir = self._pit_dir()
        argv = [
            "validate",
            "--data-dir",
            self.config.data_dir,
            "--output-dir",
            str(pit_dir),
            "--as-of-date",
            self.config.as_of_date,
            "--start-date",
            "20240102",
            "--end-date",
            self.config.as_of_date,
            "--feature-cutoff-mode",
            self.config.feature_cutoff_mode,
            "--min-listing-days",
            str(self.config.min_listing_days),
        ]
        if self.config.exclude_st:
            argv.append("--exclude-st")
        if self.config.fail_on_pit_blocker:
            argv.append("--fail-on-blocker")
        payload = _run_json_main(run_pit_main, argv)
        self.pit_summary = payload
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        output_paths = {name: str(path) for name, path in paths.items()}
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name.replace("_path", ""), path, _artifact_kind(path), "pit_validation")
        return payload, output_paths

    def _stage_matrix_cache(self) -> tuple[dict[str, Any], dict[str, str]]:
        cache_dir = self._matrix_cache_dir()
        argv = [
                "--data-dir",
                self.config.data_dir,
                "--output-dir",
                str(cache_dir),
                "--universe-name",
                self.config.universe_name,
                "--validate",
            ]
        if self.config.point_in_time:
            argv.extend(
                [
                    "--point-in-time",
                    "--feature-cutoff-mode",
                    self.config.feature_cutoff_mode,
                    "--min-listing-days",
                    str(self.config.min_listing_days),
                ]
            )
            if self.config.exclude_st:
                argv.append("--exclude-st")
            active_mask = self._pit_dir() / "active_security_mask.jsonl"
            if active_mask.exists():
                argv.extend(["--active-mask-path", str(active_mask)])
        payload = _run_json_main(run_build_matrix_main, argv)
        output_paths = {
            "matrix_metadata": str(cache_dir / "metadata.json"),
            "matrix_fields": str(cache_dir / "fields.json"),
            "matrix_ts_codes": str(cache_dir / "ts_codes.json"),
            "matrix_trade_dates": str(cache_dir / "trade_dates.json"),
            "matrix_validation_report": str(cache_dir / "matrix_validation_report.json"),
        }
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, "json", "matrix_cache")
        return payload, output_paths

    def _stage_benchmark(self) -> tuple[dict[str, Any], dict[str, str]]:
        benchmark_dir = self._benchmark_dir()
        argv = [
            "--data-dir",
            self.config.data_dir,
            "--output-dir",
            str(benchmark_dir),
        ]
        cache_dir = self._matrix_cache_dir()
        if (cache_dir / "metadata.json").exists():
            argv.extend(["--matrix-cache-dir", str(cache_dir)])
        payload = _run_json_main(run_benchmark_main, argv)
        output_paths = {
            "benchmark_result": str(benchmark_dir / "benchmark_result.json"),
            "benchmark_report": str(benchmark_dir / "benchmark_report.md"),
        }
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "benchmark")
        return payload, output_paths

    def _stage_formula_corpus(self) -> tuple[dict[str, Any], dict[str, str]]:
        corpus_dir = self._formula_corpus_dir()
        argv = [
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--output-dir",
            str(corpus_dir),
            "--artifact-dir",
            str(Path(self.config.output_dir) / "search"),
        ]
        payload = _run_json_main(run_formula_corpus_main, argv)
        output_paths = {
            "formula_corpus": str(corpus_dir / "formula_corpus.jsonl"),
            "formula_sequences": str(corpus_dir / "formula_sequences.jsonl"),
            "formula_preferences": str(corpus_dir / "formula_preferences.jsonl"),
            "formula_corpus_stats": str(corpus_dir / "formula_corpus_stats.json"),
            "formula_corpus_report": str(corpus_dir / "formula_corpus_report.md"),
            "formula_corpus_build_result": str(corpus_dir / "formula_corpus_build_result.json"),
        }
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "formula_corpus")
        return payload, output_paths

    def _stage_alphagpt_pretrain(self) -> tuple[dict[str, Any], dict[str, str]]:
        corpus_dir = self._formula_corpus_dir()
        if not (corpus_dir / "formula_sequences.jsonl").exists():
            self._stage_formula_corpus()
        pretrain_dir = self._pretrain_dir()
        argv = [
            "--sequence-path",
            str(corpus_dir / "formula_sequences.jsonl"),
            "--preference-path",
            str(corpus_dir / "formula_preferences.jsonl"),
            "--output-dir",
            str(pretrain_dir),
            "--epochs",
            str(self.config.pretrain_epochs),
            "--batch-size",
            str(self.config.pretrain_batch_size),
            "--device",
            self.config.pretrain_device,
            "--preference-steps",
            str(self.config.pretrain_preference_steps),
        ]
        if self.config.pretrain_max_sequences is not None:
            argv.extend(["--max-sequences", str(self.config.pretrain_max_sequences)])
        payload = _run_json_main(run_pretrain_main, argv)
        output_paths = {
            "alphagpt_pretrain_result": str(pretrain_dir / "alphagpt_pretrain_result.json"),
            "alphagpt_pretrain_history": str(pretrain_dir / "alphagpt_pretrain_history.jsonl"),
            "alphagpt_pretrain_report": str(pretrain_dir / "alphagpt_pretrain_report.md"),
            "alphagpt_checkpoint_manifest": str(pretrain_dir / "checkpoint_manifest.json"),
            "alphagpt_latest_checkpoint": str(pretrain_dir / "checkpoints" / "latest.pt"),
        }
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "alphagpt_pretrain")
        return payload, output_paths

    def _stage_formula_batch_eval(self) -> tuple[dict[str, Any], dict[str, str]]:
        corpus_dir = self._formula_corpus_dir()
        if not (corpus_dir / "formula_corpus.jsonl").exists():
            self._stage_formula_corpus()
        batch_dir = self._batch_eval_dir()
        argv = [
            "--data-dir",
            self.config.data_dir,
            "--universe-name",
            self.config.universe_name,
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--report-dir",
            self.config.report_dir,
            "--output-dir",
            str(batch_dir),
            "--corpus-path",
            str(corpus_dir / "formula_corpus.jsonl"),
            "--max-formulas",
            str(self.config.search_max_candidates or self.config.search_population_size),
            "--factor-transform",
            self.config.factor_transform,
            "--enable-gate",
            "--correlation-threshold",
            "0.99",
            "--min-coverage",
            "0.5",
            "--chunk-size",
            str(self.config.batch_eval_chunk_size),
            "--device",
            self.config.batch_eval_device,
            "--register-approved",
            "--continue-on-error",
        ]
        if self.config.use_matrix_cache:
            argv.extend(["--use-matrix-cache", "--matrix-cache-dir", str(self._matrix_cache_dir())])
        if self.config.use_eval_cache:
            argv.append("--use-eval-cache")
            if self.config.eval_cache_dir:
                argv.extend(["--eval-cache-dir", self.config.eval_cache_dir])
        payload = _run_json_main(run_formula_batch_eval_main, argv)
        output_paths = {
            "formula_batch_eval_result": str(batch_dir / "formula_batch_eval_result.json"),
            "formula_eval_results": str(batch_dir / "formula_eval_results.jsonl"),
            "formula_batch_eval_report": str(batch_dir / "formula_batch_eval_report.md"),
            "formula_eval_cache_manifest": str(batch_dir / "formula_eval_cache_manifest.json"),
            "formula_batch_eval_benchmark": str(batch_dir / "formula_batch_eval_benchmark.json"),
        }
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "formula_batch_eval")
        return payload, output_paths

    def _stage_formula_search(self) -> tuple[dict[str, Any], dict[str, str]]:
        payload = _run_json_main(
            run_search.main,
            [
                "--data-dir",
                self.config.data_dir,
                "--search-mode",
                self.config.search_mode,
                "--universe-name",
                self.config.universe_name,
                "--factor-store-dir",
                self.config.factor_store_dir,
                "--report-dir",
                self.config.report_dir,
                "--output-dir",
                str(Path(self.config.output_dir) / "search"),
                "--seed",
                str(self.config.search_seed),
                "--population-size",
                str(self.config.search_population_size),
                "--generations",
                str(self.config.search_generations),
                "--max-formula-len",
                "8",
                "--max-complexity",
                "24",
                "--max-lookback",
                "10",
                "--candidate-batch-size",
                str(self.config.search_max_candidates or self.config.search_population_size),
                "--neural-warmup-steps",
                str(self.config.neural_warmup_steps),
                "--neural-policy-steps",
                str(self.config.neural_policy_steps),
                "--hybrid-neural-ratio",
                str(self.config.hybrid_neural_ratio),
                "--factor-transform",
                self.config.factor_transform,
                "--enable-gate",
                "--top-k",
                str(self.config.top_k),
                "--composite-method",
                self.config.composite_method,
                "--correlation-threshold",
                "0.99",
                "--min-coverage",
                "0.5",
            ]
            + (["--corpus-sequence-path", str(self._formula_corpus_dir() / "formula_sequences.jsonl")] if (self._formula_corpus_dir() / "formula_sequences.jsonl").exists() else [])
            + (["--matrix-cache-dir", str(self._matrix_cache_dir()), "--use-matrix-cache"] if self.config.use_matrix_cache else [])
            + (
                [
                    "--point-in-time",
                    "--feature-cutoff-mode",
                    self.config.feature_cutoff_mode,
                    "--min-listing-days",
                    str(self.config.min_listing_days),
                ]
                + (["--exclude-st"] if self.config.exclude_st else [])
                if self.config.point_in_time
                else []
            )
            + (["--use-batch-eval", "--batch-eval-output-dir", str(self._batch_eval_dir()), "--batch-eval-chunk-size", str(self.config.batch_eval_chunk_size), "--batch-eval-device", self.config.batch_eval_device] if self.config.use_batch_eval else [])
            + (["--use-eval-cache"] + (["--eval-cache-dir", self.config.eval_cache_dir] if self.config.eval_cache_dir else []) if self.config.use_eval_cache else [])
            + (
                ["--neural-checkpoint", self.config.neural_checkpoint]
                if self.config.neural_checkpoint
                else (
                    ["--neural-checkpoint", str(self._pretrain_dir() / "checkpoints" / "latest.pt")]
                    if self.config.pretrain_alphagpt and (self._pretrain_dir() / "checkpoints" / "latest.pt").exists()
                    else []
                )
            ),
        )
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        output_paths = {
            "factors": str(Path(self.config.factor_store_dir) / "factors.jsonl"),
            "experiments": str(Path(self.config.factor_store_dir) / "experiments.jsonl"),
        }
        if self.config.search_mode == "neural":
            output_paths.update(
                {
                    "neural_search_result": paths.get("neural_search_result_path", str(Path(self.config.output_dir) / "search" / "neural_search_result.json")),
                    "neural_training_history": paths.get("neural_training_history_path", str(Path(self.config.output_dir) / "search" / "neural_training_history.jsonl")),
                    "neural_search_report": paths.get("neural_search_report_path", str(Path(self.config.output_dir) / "search" / "neural_search_report.md")),
                    "neural_checkpoint_dir": paths.get("checkpoint_dir", str(Path(self.config.output_dir) / "search" / "checkpoints")),
                }
            )
        else:
            output_paths.update(
                {
                    "search_result": paths.get("search_result_path", str(Path(self.config.output_dir) / "search" / "search_result.json")),
                    "search_candidates": paths.get("search_candidates_path", str(Path(self.config.output_dir) / "search" / "search_candidates.jsonl")),
                    "search_report": paths.get("search_report_json_path", str(Path(self.config.output_dir) / "search" / "search_report.json")),
                    "search_report_markdown": paths.get("search_report_md_path", str(Path(self.config.output_dir) / "search" / "search_report.md")),
                }
            )
            neural_metadata = payload.get("neural_metadata") if isinstance(payload.get("neural_metadata"), dict) else {}
            neural_paths = neural_metadata.get("paths") if isinstance(neural_metadata.get("paths"), dict) else {}
            if neural_paths:
                output_paths.update(
                    {
                        "neural_search_result": neural_paths.get("neural_search_result_path", ""),
                        "neural_training_history": neural_paths.get("neural_training_history_path", ""),
                        "neural_search_report": neural_paths.get("neural_search_report_path", ""),
                        "neural_checkpoint_dir": neural_paths.get("checkpoint_dir", ""),
                    }
                )
        for name, path in output_paths.items():
            if not path:
                continue
            kind = "markdown" if path.endswith(".md") else ("jsonl" if path.endswith(".jsonl") else "json")
            self.catalog = register_artifact(self.catalog, name, path, kind, "formula_search")
        self.selected_factor_id = _select_latest_composite(self.config.factor_store_dir)
        return payload, output_paths

    def _stage_backtest(self) -> tuple[dict[str, Any], dict[str, str]]:
        argv = [
            "--data-dir",
            self.config.data_dir,
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--output-dir",
            self.config.backtest_dir,
            "--latest-approved",
            "--factor-type",
            "composite",
            "--top-n",
            "2",
            "--max-weight",
            "0.10",
            "--portfolio-method",
            self.config.portfolio_method,
            "--index-code",
            self.config.index_code,
            "--risk-aversion",
            str(self.config.risk_aversion),
            "--turnover-penalty",
            str(self.config.turnover_penalty),
            "--max-turnover",
            str(self.config.max_turnover),
            "--max-industry-active-weight",
            str(self.config.max_industry_active_weight),
            "--max-tracking-error",
            str(self.config.max_tracking_error),
            "--risk-report-dir",
            str(Path(self.config.backtest_dir) / "risk"),
        ]
        if self.config.use_factor_risk_model:
            argv.extend(["--use-factor-risk-model", "--risk-model-shrinkage", str(self.config.risk_model_shrinkage)])
        if self.config.risk_model_lookback is not None:
            argv.extend(["--risk-model-lookback", str(self.config.risk_model_lookback)])
        if self.config.attribution:
            argv.append("--attribution")
        if self.config.max_style_exposure is not None:
            argv.extend(["--max-style-exposure", str(self.config.max_style_exposure)])
        if self.config.max_active_style_exposure is not None:
            argv.extend(["--max-active-style-exposure", str(self.config.max_active_style_exposure)])
        if self.config.max_factor_risk_contribution is not None:
            argv.extend(["--max-factor-risk-contribution", str(self.config.max_factor_risk_contribution)])
        if self.config.point_in_time:
            argv.extend(
                [
                    "--point-in-time",
                    "--feature-cutoff-mode",
                    self.config.feature_cutoff_mode,
                    "--signal-lag-days",
                    "1",
                    "--min-listing-days",
                    str(self.config.min_listing_days),
                ]
            )
            if self.config.exclude_st:
                argv.append("--exclude-st")
        if self.config.run_leakage_audit:
            argv.extend(["--run-leakage-audit", "--leakage-audit-dir", str(self._backtest_leakage_dir())])
            if self.config.fail_on_leakage_blocker:
                argv.append("--fail-on-leakage-blocker")

        payload = _run_json_main(run_backtest.main, argv)
        self.backtest_summary = payload
        self.selected_factor_id = str(payload.get("factor_id") or self.selected_factor_id)
        output_paths = {
            "backtest_result": str(Path(self.config.backtest_dir) / "backtest_result.json"),
            "equity_curve": str(Path(self.config.backtest_dir) / "equity_curve.jsonl"),
            "trades": str(Path(self.config.backtest_dir) / "trades.jsonl"),
        }
        if payload.get("risk_report_path"):
            output_paths["risk_report"] = str(payload["risk_report_path"])
        if payload.get("risk_report_md_path"):
            output_paths["risk_report_markdown"] = str(payload["risk_report_md_path"])
        if payload.get("optimization_result_path"):
            output_paths["optimization_result"] = str(payload["optimization_result_path"])
        if payload.get("risk_exposures_path"):
            output_paths["risk_exposures"] = str(payload["risk_exposures_path"])
        if payload.get("risk_decomposition_path"):
            output_paths["risk_decomposition"] = str(payload["risk_decomposition_path"])
        if payload.get("return_attribution_path"):
            output_paths["return_attribution"] = str(payload["return_attribution_path"])
        if payload.get("leakage_audit_report_path"):
            output_paths["backtest_leakage_audit_report"] = str(payload["leakage_audit_report_path"])
        if payload.get("truncation_consistency_report_path"):
            output_paths["backtest_truncation_consistency"] = str(payload["truncation_consistency_report_path"])
        if self.selected_factor_id:
            output_paths["selected_factor_values"] = str(
                Path(self.config.factor_store_dir) / "factor_values" / f"{self.selected_factor_id}.jsonl"
            )
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "backtest")
        return payload, output_paths

    def _stage_leakage_audit(self) -> tuple[dict[str, Any], dict[str, str]]:
        leakage_dir = self._leakage_dir()
        argv = [
            "--data-dir",
            self.config.data_dir,
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--output-dir",
            str(leakage_dir),
            "--as-of-date",
            self.config.as_of_date,
            "--cutoff-date",
            self.config.as_of_date,
            "--universe-name",
            self.config.universe_name,
            "--run-static-scan",
            "--run-truncation-test",
            "--max-formulas",
            str(self.config.search_max_candidates or 5),
            "--backtest-result-path",
            str(Path(self.config.backtest_dir) / "backtest_result.json"),
        ]
        if self.config.point_in_time:
            argv.extend(["--point-in-time", "--feature-cutoff-mode", self.config.feature_cutoff_mode])
            if self.config.exclude_st:
                argv.append("--exclude-st")
        if self.config.fail_on_leakage_blocker:
            argv.append("--fail-on-blocker")
        payload = _run_json_main(run_leakage_audit_main, argv)
        self.leakage_summary = payload
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        output_paths = {name: str(path) for name, path in paths.items()}
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name.replace("_path", ""), path, _artifact_kind(path), "leakage_audit")
        return payload, output_paths

    def _stage_orders(self) -> tuple[dict[str, Any], dict[str, str]]:
        argv = [
            "--data-dir",
            self.config.data_dir,
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--output-dir",
            self.config.orders_dir,
            "--latest-approved",
            "--factor-type",
            "composite",
            "--top-n",
            "2",
            "--max-weight",
            "0.10",
            "--portfolio-value",
            "1000000",
            "--portfolio-method",
            self.config.portfolio_method,
            "--index-code",
            self.config.index_code,
            "--risk-aversion",
            str(self.config.risk_aversion),
            "--turnover-penalty",
            str(self.config.turnover_penalty),
            "--max-turnover",
            str(self.config.max_turnover),
            "--max-industry-active-weight",
            str(self.config.max_industry_active_weight),
            "--max-tracking-error",
            str(self.config.max_tracking_error),
        ]
        if self.config.use_factor_risk_model:
            argv.extend(["--use-factor-risk-model", "--risk-model-shrinkage", str(self.config.risk_model_shrinkage)])
        if self.config.risk_model_lookback is not None:
            argv.extend(["--risk-model-lookback", str(self.config.risk_model_lookback)])
        if self.config.max_style_exposure is not None:
            argv.extend(["--max-style-exposure", str(self.config.max_style_exposure)])
        if self.config.max_active_style_exposure is not None:
            argv.extend(["--max-active-style-exposure", str(self.config.max_active_style_exposure)])
        if self.config.point_in_time:
            argv.extend(
                [
                    "--point-in-time",
                    "--feature-cutoff-mode",
                    self.config.feature_cutoff_mode,
                    "--min-listing-days",
                    str(self.config.min_listing_days),
                ]
            )
            if self.config.exclude_st:
                argv.append("--exclude-st")

        payload = _run_json_main(strategy_runner.main, argv)
        output_paths = {
            "target_positions": str(Path(self.config.orders_dir) / "target_positions.jsonl"),
            "orders": str(Path(self.config.orders_dir) / "orders.jsonl"),
            "paper_fills": str(Path(self.config.orders_dir) / "paper_fills.jsonl"),
        }
        if payload.get("risk_report_path"):
            output_paths["orders_risk_report"] = str(payload["risk_report_path"])
        if payload.get("optimization_result_path"):
            output_paths["orders_optimization_result"] = str(payload["optimization_result_path"])
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "orders")
        return payload, output_paths

    def _stage_walk_forward(self) -> tuple[dict[str, Any], dict[str, str]]:
        if not self.selected_factor_id:
            raise ValueError("no selected factor for walk-forward evaluation")
        loader = AShareDataLoader(
            data_dir=self.config.data_dir,
            device="cpu",
            universe_name=self.config.universe_name,
            matrix_cache_dir=self._matrix_cache_dir(),
            use_matrix_cache=self.config.use_matrix_cache,
            point_in_time=self.config.point_in_time,
            feature_cutoff_mode=self.config.feature_cutoff_mode,
            min_listing_days=self.config.min_listing_days,
            exclude_st=self.config.exclude_st,
        ).load_data()
        store = LocalFactorStore(self.config.factor_store_dir)
        windows = build_walk_forward_windows(
            loader.trade_dates,
            self.config.walk_forward_train_size,
            self.config.walk_forward_test_size,
            self.config.walk_forward_step_size,
        )
        result = evaluate_factor_walk_forward(loader, store, self.selected_factor_id, windows)
        path = Path(self.config.output_dir) / "walk_forward_result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.catalog = register_artifact(self.catalog, "walk_forward_result", path, "json", "walk_forward")
        return result.to_dict(), {"walk_forward_result": str(path)}

    def _stage_promotion(self) -> tuple[dict[str, Any], dict[str, str]]:
        if not self.selected_factor_id:
            raise ValueError("no selected factor for promotion")
        walk_path = Path(self.config.output_dir) / "walk_forward_result.json"
        walk_payload = _read_json(walk_path)
        from .models import WalkForwardResult

        walk_result = WalkForwardResult(
            factor_id=self.selected_factor_id,
            windows=walk_payload.get("windows", []),
            summary=walk_payload.get("summary", {}),
        )
        store = LocalFactorStore(self.config.factor_store_dir)
        self.promotion_decision = promote_factor_if_eligible(
            store,
            self.selected_factor_id,
            walk_result,
            self.backtest_summary.get("metrics", {}),
            PromotionConfig(
                max_active_style_exposure_abs=self.config.max_active_style_exposure
                if self.config.max_active_style_exposure is not None
                else 999.0,
                max_factor_risk_share=self.config.max_factor_risk_contribution
                if self.config.max_factor_risk_contribution is not None
                else 1.0,
            ),
        )
        path = write_promotion_decision(self.promotion_decision, self.config.output_dir)
        self.catalog = register_artifact(self.catalog, "promotion_decision", path, "json", "promotion")
        return self.promotion_decision.to_dict(), {"promotion_decision": str(path)}

    def _stage_model_registry(self) -> tuple[dict[str, Any], dict[str, str]]:
        if not self.selected_factor_id:
            raise ValueError("no selected factor for model registry registration")
        store = LocalFactorStore(self.config.factor_store_dir)
        factor = next((record for record in store.load_factors() if record.factor_id == self.selected_factor_id), None)
        if factor is None:
            raise FileNotFoundError(f"selected factor not found: {self.selected_factor_id}")
        lifecycle_status = "production_candidate" if self.promotion_decision and self.promotion_decision.passed else "research_candidate"
        registry = LocalModelRegistry(self._model_registry_dir())
        source_artifacts = {entry.name: entry.path for entry in self.catalog.entries}
        model = registry.register_factor_record(
            factor,
            model_kind=ModelKind.composite_factor,
            source_artifacts=source_artifacts,
            metrics=factor.metrics,
            metadata={
                "suite_name": self.config.suite_name,
                "promotion_decision": self.promotion_decision.to_dict() if self.promotion_decision else {},
                "point_in_time": self.config.point_in_time,
                "feature_cutoff_mode": self.config.feature_cutoff_mode,
                "pit_summary": self.pit_summary,
                "leakage_summary": self.leakage_summary,
            },
            lifecycle_status=lifecycle_status,
        )
        self.model_version_id = model.model_version_id
        registry.sync_factor_store_status(store, model.model_version_id)
        report_json, report_md = write_model_registry_report(registry)
        output_paths = {
            "model_versions": str(registry.versions_path),
            "model_state": str(registry.state_path),
            "model_deployments": str(registry.deployments_path),
            "lifecycle_events": str(registry.events_path),
            "model_registry_manifest": str(registry.manifest_path),
            "model_registry_report": str(report_json),
            "model_registry_report_md": str(report_md),
            "model_lineage_graph": str(self._model_registry_dir() / "model_lineage_graph.json"),
        }
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "model_registry")
        return model.to_dict(), output_paths

    def _stage_model_lifecycle(self) -> tuple[dict[str, Any], dict[str, str]]:
        if not self.model_version_id:
            self._stage_model_registry()
        argv = [
            "propose-activation",
            "--data-dir",
            self.config.data_dir,
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--registry-dir",
            str(self._model_registry_dir()),
            "--approval-store-dir",
            str(self._model_approval_store_dir()),
            "--output-dir",
            str(self._model_lifecycle_dir()),
            "--model-version-id",
            str(self.model_version_id),
            "--as-of-date",
            self.config.as_of_date,
            "--promotion-decision-path",
            str(Path(self.config.output_dir) / "promotion_decision.json"),
            "--backtest-result-path",
            str(Path(self.config.backtest_dir) / "backtest_result.json"),
            "--artifact-catalog-path",
            str(Path(self.config.output_dir) / "artifact_catalog.json"),
            "--create-review-package",
        ]
        pit_report = self._pit_dir() / "pit_validation_report.json"
        survivorship_report = self._pit_dir() / "survivorship_bias_report.json"
        leakage_report = self._leakage_dir() / "leakage_audit_report.json"
        truncation_report = self._leakage_dir() / "truncation_consistency_report.json"
        if pit_report.exists():
            argv.extend(["--pit-validation-report-path", str(pit_report)])
        if survivorship_report.exists():
            argv.extend(["--survivorship-report-path", str(survivorship_report)])
        if leakage_report.exists():
            argv.extend(["--leakage-audit-report-path", str(leakage_report)])
        if truncation_report.exists():
            argv.extend(["--truncation-consistency-report-path", str(truncation_report)])
        if self.config.model_lifecycle_policy_path:
            argv.extend(["--policy-path", self.config.model_lifecycle_policy_path])
        if self.config.require_model_approval:
            argv.append("--require-approval")
        payload = _run_json_main(run_lifecycle_main, argv)
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        self.model_lifecycle_summary = {
            "approval_id": payload.get("approval_id"),
            "recommended_action": (payload.get("decision") or {}).get("recommended_action"),
            "current_status": (payload.get("decision") or {}).get("current_status"),
            **paths,
        }
        output_paths = {
            "factor_lifecycle_report": paths.get("factor_lifecycle_report_path", str(self._model_lifecycle_dir() / "factor_lifecycle_report.json")),
            "factor_lifecycle_report_md": paths.get("factor_lifecycle_report_md_path", str(self._model_lifecycle_dir() / "factor_lifecycle_report.md")),
            "lifecycle_decisions": paths.get("lifecycle_decisions_path", str(self._model_lifecycle_dir() / "lifecycle_decisions.jsonl")),
            "factor_health_checks": paths.get("factor_health_checks_path", str(self._model_lifecycle_dir() / "factor_health_checks.jsonl")),
            "model_review_package": paths.get("model_review_package_path", str(self._model_lifecycle_dir() / "model_review_package.json")),
            "model_review_package_md": paths.get("model_review_package_md_path", str(self._model_lifecycle_dir() / "model_review_package.md")),
            "model_lineage_graph": paths.get("model_lineage_graph_path", str(self._model_registry_dir() / "model_lineage_graph.json")),
        }
        for name, path in output_paths.items():
            if path:
                self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "model_lifecycle")
        return payload, output_paths

    def _matrix_cache_dir(self) -> Path:
        return Path(self.config.matrix_cache_dir) if self.config.matrix_cache_dir else Path(self.config.data_dir) / "matrix_cache"

    def _benchmark_dir(self) -> Path:
        return Path(self.config.benchmark_dir) if self.config.benchmark_dir else Path(self.config.output_dir) / "benchmark"

    def _formula_corpus_dir(self) -> Path:
        return Path(self.config.formula_corpus_dir) if self.config.formula_corpus_dir else Path(self.config.output_dir) / "formula_corpus"

    def _pretrain_dir(self) -> Path:
        return Path(self.config.pretrain_dir) if self.config.pretrain_dir else Path(self.config.output_dir) / "alphagpt_pretrain"

    def _batch_eval_dir(self) -> Path:
        return Path(self.config.batch_eval_dir) if self.config.batch_eval_dir else Path(self.config.output_dir) / "formula_batch_eval"

    def _model_registry_dir(self) -> Path:
        return Path(self.config.model_registry_dir) if self.config.model_registry_dir else Path(self.config.output_dir).parent / "model_registry"

    def _model_lifecycle_dir(self) -> Path:
        return Path(self.config.model_lifecycle_output_dir) if self.config.model_lifecycle_output_dir else Path(self.config.output_dir) / "model_lifecycle"

    def _model_approval_store_dir(self) -> Path:
        return Path(self.config.model_approval_store_dir) if self.config.model_approval_store_dir else Path(self.config.output_dir).parent / "approvals"

    def _pit_dir(self) -> Path:
        return Path(self.config.pit_output_dir) if self.config.pit_output_dir else Path(self.config.output_dir) / "pit"

    def _leakage_dir(self) -> Path:
        return Path(self.config.leakage_audit_dir) if self.config.leakage_audit_dir else Path(self.config.output_dir) / "leakage_audit"

    def _backtest_leakage_dir(self) -> Path:
        return Path(self.config.backtest_dir) / "leakage_audit"


def _run_json_main(main_func: Callable[[list[str] | None], int], argv: list[str]) -> dict[str, Any]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = main_func(argv)
    output = buffer.getvalue().strip()
    if exit_code != 0:
        raise RuntimeError(f"stage command failed with exit code {exit_code}: {' '.join(argv)}")
    if not output:
        return {}
    return json.loads(output)


def _select_latest_composite(factor_store_dir: str) -> str | None:
    record = LocalFactorStore(factor_store_dir).load_latest_factor(status="approved", factor_type="composite")
    return record.factor_id if record is not None else None


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _artifact_kind(path: str) -> str:
    if path.endswith(".md"):
        return "markdown"
    if path.endswith(".jsonl"):
        return "jsonl"
    return "json"
