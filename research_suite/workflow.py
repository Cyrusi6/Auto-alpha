"""One-click research suite workflow."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backtest import run_backtest
from corporate_actions.run_actions import main as run_corporate_actions_main
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
from validation_lab.run_validation import main as run_validation_main
from factor_certification.run_certify import main as run_certify_main
from portfolio_lab.run_portfolio_lab import main as run_portfolio_lab_main
from portfolio_certification.run_portfolio_certify import main as run_portfolio_certify_main
from leakage_audit.run_audit import main as run_leakage_audit_main
from point_in_time.run_pit import main as run_pit_main
from data_lake import LocalDataLakeRegistry, create_research_freeze, validate_research_input
from data_lake.fingerprint import content_hash_for_fingerprints, fingerprint_data_dir
from data_lake.models import DatasetVersionRecord
from data_lake.report import write_data_lake_report, write_dataset_version_manifest
from data_lake.freeze import write_freeze_validation_report
from experiment_orchestrator.workflows import run_workflow_smoke
from alpha_factory.run_factory import main as run_alpha_factory_main

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
        self.corporate_action_summary: dict[str, Any] = {}
        self.compute_summary: dict[str, Any] = {}
        self.alpha_summary: dict[str, Any] = {}
        self.validation_summary: dict[str, Any] = {}
        self.certification_summary: dict[str, Any] = {}
        self.portfolio_lab_summary: dict[str, Any] = {}
        self.portfolio_certification_summary: dict[str, Any] = {}
        self.dataset_version_id: str | None = None
        self.data_freeze_id: str | None = config.data_freeze_id
        self.data_freeze_hash: str | None = None
        self.freeze_validation_status: str = "not_run"
        self.data_hash_drift_count: int = 0
        if config.data_freeze_dir:
            report = validate_research_input(config.data_dir, config.data_freeze_dir, config.require_data_freeze)
            self.data_freeze_id = config.data_freeze_id or report.freeze_id
            self.data_freeze_hash = report.content_hash
            self.freeze_validation_status = report.status
            self.data_hash_drift_count = report.error_count
            if report.error_count and config.fail_on_freeze_error:
                raise RuntimeError("data freeze validation failed")
            self.config = replace(config, data_dir=str(Path(config.data_freeze_dir) / "data"))

    def run(self) -> ResearchSuiteResult:
        started_at = _utc_now()
        status = "success"
        try:
            if not self.config.skip_data_sync:
                self._append_stage("data_sync", self._stage_data_sync)
            if self.config.run_corporate_action_report:
                self._append_stage("corporate_actions", self._stage_corporate_actions)
            if not self.config.skip_universe:
                self._append_stage("universe", self._stage_universe)
            if self.config.run_pit_validation:
                self._append_stage("pit_validation", self._stage_pit_validation)
            if self.config.create_data_version:
                self._append_stage("data_version", self._stage_data_version)
            if self.config.create_research_freeze:
                self._append_stage("data_freeze", self._stage_data_freeze)
            if self.config.validate_data_freeze and self.config.data_freeze_dir:
                self._append_stage("freeze_validation", self._stage_freeze_validation)
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
            if self.config.use_compute_scheduler:
                self._append_stage("compute_experiment", self._stage_compute_experiment)
            if self.config.run_alpha_factory:
                self._append_stage("alpha_factory", self._stage_alpha_factory)
            self._append_stage("formula_search", self._stage_formula_search)
            self._append_stage("backtest", self._stage_backtest)
            if self.config.run_leakage_audit:
                self._append_stage("leakage_audit", self._stage_leakage_audit)
            if not self.config.skip_orders:
                self._append_stage("orders", self._stage_orders)
            self._append_stage("walk_forward", self._stage_walk_forward)
            if self.config.run_validation_lab:
                self._append_stage("validation_lab", self._stage_validation_lab)
            if self.config.run_factor_certification:
                self._append_stage("factor_certification", self._stage_factor_certification)
            if self.config.run_portfolio_lab:
                self._append_stage("portfolio_lab", self._stage_portfolio_lab)
            if self.config.run_portfolio_certification:
                self._append_stage("portfolio_certification", self._stage_portfolio_certification)
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
                "corporate_action_aware": self.config.corporate_action_aware,
                "target_return_mode": self.config.target_return_mode,
                "corporate_action_event_count": self.corporate_action_summary.get("event_count", 0),
                "implemented_action_count": self.corporate_action_summary.get("implemented_action_count", 0),
                "corporate_action_error_count": self.corporate_action_summary.get("error_count", 0),
                "adjustment_reconciliation_warning_count": self.corporate_action_summary.get(
                    "adjustment_reconciliation_warning_count", 0
                ),
                "settlement_aware": self.config.settlement_aware,
                "settlement_profile": self.config.settlement_profile,
                "dataset_version_id": self.dataset_version_id or _dataset_version_id(self.config.data_version_manifest_path),
                "data_freeze_id": self.data_freeze_id,
                "data_freeze_dir": self.config.data_freeze_dir,
                "data_freeze_hash": self.data_freeze_hash,
                "freeze_validation_status": self.freeze_validation_status,
                "data_quality_error_count": _quality_error_count(Path(self.config.data_dir) / "quality_report.json"),
                "data_hash_drift_count": self.data_hash_drift_count,
                "compute_scheduler_enabled": self.config.use_compute_scheduler,
                "compute_run_id": self.compute_summary.get("run_id"),
                "experiment_id": self.compute_summary.get("experiment_id"),
                "gpu_count_detected": self.compute_summary.get("gpu_count_detected", 0),
                "gpu_count_used": self.compute_summary.get("gpu_count_used", 0),
                "shard_count": self.compute_summary.get("shard_count", self.config.shard_count),
                "compute_success_count": self.compute_summary.get("compute_success_count", 0),
                "compute_failed_count": self.compute_summary.get("compute_failed_count", 0),
                "compute_resumed_count": self.compute_summary.get("compute_resumed_count", 0),
                "total_gpu_allocated_seconds": self.compute_summary.get("total_gpu_allocated_seconds", 0.0),
                "formula_eval_throughput": self.compute_summary.get("formula_eval_throughput", 0.0),
                "pretrain_samples_per_second": self.compute_summary.get("pretrain_samples_per_second", 0.0),
                "fallback_to_cpu_count": self.compute_summary.get("fallback_to_cpu_count", 0),
                "cuda_oom_count": self.compute_summary.get("cuda_oom_count", 0),
                "experiment_plan_path": self.compute_summary.get("experiment_plan_path"),
                "compute_run_report_path": self.compute_summary.get("compute_run_report_path"),
                "experiment_merge_report_path": self.compute_summary.get("experiment_merge_report_path"),
                "alpha_factory_enabled": self.config.run_alpha_factory,
                "alpha_campaign_id": self.alpha_summary.get("campaign_id"),
                "alpha_candidates_generated": self.alpha_summary.get("candidates_generated", 0),
                "alpha_candidates_static_passed": self.alpha_summary.get("static_passed", 0),
                "alpha_proxy_passed": self.alpha_summary.get("proxy_passed", 0),
                "alpha_full_eval_count": self.alpha_summary.get("full_eval_count", 0),
                "alpha_shortlist_count": self.alpha_summary.get("shortlist_count", 0),
                "alpha_best_score": self.alpha_summary.get("best_score", 0.0),
                "alpha_feature_set_name": self.alpha_summary.get("feature_set_name"),
                "alpha_feature_count": self.alpha_summary.get("feature_count", 0),
                "alpha_family_distribution": self.alpha_summary.get("family_distribution", {}),
                "alpha_compute_run_report_path": self.alpha_summary.get("compute_run_report_path"),
                "alpha_factory_report_path": self.alpha_summary.get("alpha_factory_report_path"),
                "validation_lab_enabled": self.config.run_validation_lab,
                "validation_status": self.validation_summary.get("status"),
                "validation_blocker_count": self.validation_summary.get("validation_blocker_count", 0),
                "pbo_estimate": self.validation_summary.get("pbo_estimate", 0.0),
                "deflated_ic_score": self.validation_summary.get("deflated_ic_score", 0.0),
                "placebo_percentile": self.validation_summary.get("placebo_percentile", 0.0),
                "regime_pass_ratio": self.validation_summary.get("regime_pass_ratio", 0.0),
                "sensitivity_pass_ratio": self.validation_summary.get("sensitivity_pass_ratio", 0.0),
                "stress_backtest_pass_ratio": self.validation_summary.get("stress_backtest_pass_ratio", 0.0),
                "certification_status": self.certification_summary.get("certification_status"),
                "certification_policy_profile": self.certification_summary.get("certification_policy_profile"),
                "certification_decision_path": (self.certification_summary.get("paths") or {}).get("factor_certification_decision_path"),
                "certification_blocker_count": self.certification_summary.get("certification_blocker_count", 0),
                "certification_required_remediation_count": self.certification_summary.get("certification_required_remediation_count", 0),
                "portfolio_lab_enabled": self.config.run_portfolio_lab,
                "portfolio_lab_status": self.portfolio_lab_summary.get("status"),
                "selected_portfolio_policy_id": self.portfolio_lab_summary.get("selected_policy_id"),
                "portfolio_lab_trial_count": self.portfolio_lab_summary.get("trial_count", 0),
                "portfolio_certification_status": self.portfolio_certification_summary.get("certification_status"),
                "portfolio_certification_policy_profile": self.portfolio_certification_summary.get("portfolio_certification_policy_profile"),
                "portfolio_certification_decision_path": (self.portfolio_certification_summary.get("paths") or {}).get("portfolio_certification_decision_path"),
                "portfolio_policy_model_version_id": self.portfolio_certification_summary.get("model_version_id"),
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
        argv = [
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
        ]
        if self.config.include_corporate_actions:
            argv.extend(["--include-corporate-actions", "--corporate-action-cash-field", self.config.corporate_action_cash_field])
        else:
            argv.append("--no-corporate-actions")
        payload = _run_json_main(run_pipeline.main, argv)
        output_paths = {
            "manifest": str(Path(self.config.data_dir) / "manifest.json"),
            "quality_report": str(Path(self.config.data_dir) / "quality_report.json"),
            "pipeline_state": str(Path(self.config.data_dir) / "pipeline_state.json"),
        }
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, "json", "data_sync")
        return payload, output_paths

    def _stage_corporate_actions(self) -> tuple[dict[str, Any], dict[str, str]]:
        action_dir = self._corporate_action_dir()
        argv = [
            "report",
            "--data-dir",
            self.config.data_dir,
            "--output-dir",
            str(action_dir),
            "--start-date",
            "00000000",
            "--end-date",
            self.config.as_of_date,
            "--cash-field",
            self.config.corporate_action_cash_field,
        ]
        if self.config.reconcile_adjustment_factors:
            argv.append("--reconcile-adjustment")
        if self.config.fail_on_corporate_action_error:
            argv.append("--fail-on-error")
        payload = _run_json_main(run_corporate_actions_main, argv)
        summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else payload
        self.corporate_action_summary = dict(summary)
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        output_paths = {name: str(path) for name, path in paths.items()}
        for name, path in output_paths.items():
            self.catalog = register_artifact(
                self.catalog,
                name.replace("_path", ""),
                path,
                _artifact_kind(path),
                "corporate_actions",
            )
        return payload, output_paths

    def _stage_universe(self) -> tuple[dict[str, Any], dict[str, str]]:
        frozen_universe = Path(self.config.data_dir) / "universe" / f"{self.config.universe_name}.jsonl"
        frozen_summary = Path(self.config.data_dir) / "universe" / f"{self.config.universe_name}_summary.json"
        if self.config.data_freeze_dir and frozen_universe.exists() and frozen_summary.exists():
            payload = _read_json(frozen_summary) or {}
            output_paths = {
                "universe": str(frozen_universe),
                "universe_summary": str(frozen_summary),
            }
            pit_summary = Path(self.config.data_dir) / "universe" / f"{self.config.universe_name}_pit_summary.json"
            if pit_summary.exists():
                output_paths["universe_pit_summary"] = str(pit_summary)
            for name, path in output_paths.items():
                self.catalog = register_artifact(self.catalog, name, path, "jsonl" if name == "universe" else "json", "universe")
            return payload | {"reused_from_freeze": True}, output_paths

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

    def _stage_data_version(self) -> tuple[dict[str, Any], dict[str, str]]:
        registry = LocalDataLakeRegistry(self._data_lake_registry_dir())
        datasets = [
            "securities",
            "trade_calendar",
            "daily_bars",
            "daily_basic",
            "financial_features",
            "daily_limits",
            "adjustment_factors",
            "index_members",
            "corporate_actions",
        ]
        fingerprints = fingerprint_data_dir(self.config.data_dir, datasets)
        content_hash = content_hash_for_fingerprints(fingerprints)
        version_id = f"dsver_{hashlib.sha256(content_hash.encode('utf-8')).hexdigest()[:16]}"
        version = DatasetVersionRecord(
            dataset_version_id=version_id,
            provider=self.config.provider,
            data_dir=self.config.data_dir,
            start_date="20240102",
            end_date=self.config.as_of_date,
            datasets=[item.dataset for item in fingerprints],
            dataset_fingerprints=[item.to_dict() for item in fingerprints],
            quality_report_path=str(Path(self.config.data_dir) / "quality_report.json"),
            dataset_stats_path=str(Path(self.config.data_dir) / "dataset_stats.json"),
            pit_validation_report_path=str(self._pit_dir() / "pit_validation_report.json") if (self._pit_dir() / "pit_validation_report.json").exists() else None,
            corporate_actions_report_path=str(self._corporate_action_dir() / "corporate_actions_report.json") if (self._corporate_action_dir() / "corporate_actions_report.json").exists() else None,
            created_at=_utc_now(),
            status="validated",
            content_hash=content_hash,
            metadata={"suite_name": self.config.suite_name},
        )
        version = registry.register_dataset_version(version)
        self.dataset_version_id = version.dataset_version_id
        version_manifest = write_dataset_version_manifest(version, self._data_version_dir())
        lake_json, lake_md = write_data_lake_report(registry, self._data_version_dir())
        self.config = replace(self.config, data_version_manifest_path=str(version_manifest))
        output_paths = {
            "dataset_version_manifest": str(version_manifest),
            "data_lake_report": str(lake_json),
            "data_lake_report_md": str(lake_md),
            "dataset_versions": str(registry.versions_path),
            "data_lake_events": str(registry.events_path),
        }
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "data_version")
        return {"dataset_version_id": version.dataset_version_id, "content_hash": version.content_hash}, output_paths

    def _stage_data_freeze(self) -> tuple[dict[str, Any], dict[str, str]]:
        registry = LocalDataLakeRegistry(self._data_lake_registry_dir())
        version = registry.get_dataset_version(self.dataset_version_id or "") or registry.latest_dataset_version(provider=self.config.provider)
        if version is None:
            raise RuntimeError("dataset version is required before creating a research freeze")
        freeze_dir = Path(self.config.research_freeze_dir) if self.config.research_freeze_dir else Path(self.config.output_dir).parent / "freezes" / self.config.suite_name
        freeze = create_research_freeze(
            self.config.data_dir,
            freeze_dir,
            version,
            freeze_name=freeze_dir.name,
            mode=self.config.freeze_mode,
            artifact_paths={
                "quality_report_path": str(Path(self.config.data_dir) / "quality_report.json"),
                "pit_validation_report_path": str(self._pit_dir() / "pit_validation_report.json") if (self._pit_dir() / "pit_validation_report.json").exists() else None,
                "corporate_actions_report_path": str(self._corporate_action_dir() / "corporate_actions_report.json") if (self._corporate_action_dir() / "corporate_actions_report.json").exists() else None,
            },
            matrix_cache_dir=self.config.matrix_cache_dir,
        )
        freeze = registry.register_freeze(freeze)
        validation = validate_research_input(data_freeze_dir=freeze.freeze_dir, require_freeze=True)
        validation_path = write_freeze_validation_report(validation, self._freeze_validation_dir())
        self.data_freeze_id = freeze.freeze_id
        self.data_freeze_hash = freeze.content_hash
        self.freeze_validation_status = validation.status
        self.data_hash_drift_count = validation.error_count
        if validation.error_count and self.config.fail_on_freeze_error:
            raise RuntimeError("research freeze validation failed")
        self.config = replace(
            self.config,
            data_dir=str(Path(freeze.freeze_dir) / "data"),
            data_freeze_dir=freeze.freeze_dir,
            data_freeze_id=freeze.freeze_id,
            data_version_manifest_path=str(Path(freeze.freeze_dir) / "dataset_version_manifest.json"),
            freeze_validation_report_path=str(validation_path),
        )
        lake_json, lake_md = write_data_lake_report(registry, self._data_version_dir())
        output_paths = {
            "research_data_freeze": str(Path(freeze.freeze_dir) / "research_data_freeze.json"),
            "freeze_manifest": str(Path(freeze.freeze_dir) / "freeze_manifest.json"),
            "freeze_validation_report": str(validation_path),
            "dataset_version_manifest": str(Path(freeze.freeze_dir) / "dataset_version_manifest.json"),
            "data_lake_report": str(lake_json),
            "data_lake_report_md": str(lake_md),
            "research_freezes": str(registry.freezes_path),
        }
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "data_freeze")
        return freeze.to_dict() | {"freeze_validation_status": validation.status}, output_paths

    def _stage_freeze_validation(self) -> tuple[dict[str, Any], dict[str, str]]:
        report = validate_research_input(self.config.data_dir, self.config.data_freeze_dir, require_freeze=True)
        path = write_freeze_validation_report(report, self._freeze_validation_dir())
        self.freeze_validation_status = report.status
        self.data_hash_drift_count = report.error_count
        if report.error_count and self.config.fail_on_freeze_error:
            raise RuntimeError("research freeze validation failed")
        output_paths = {"freeze_validation_report": str(path)}
        self.catalog = register_artifact(self.catalog, "freeze_validation_report", path, "json", "freeze_validation")
        return report.to_dict(), output_paths

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
        if self.config.data_freeze_dir:
            argv.extend(
                [
                    "--data-freeze-dir",
                    self.config.data_freeze_dir,
                    "--data-version-manifest-path",
                    self.config.data_version_manifest_path or "",
                    "--require-data-freeze",
                ]
            )
            if self.config.data_freeze_id:
                argv.extend(["--data-freeze-id", self.config.data_freeze_id])
            argv.append("--write-matrix-version-manifest")
        if self.config.corporate_action_aware:
            argv.extend(
                [
                    "--corporate-action-aware",
                    "--target-return-mode",
                    self.config.target_return_mode,
                    "--corporate-action-dir",
                    str(self._corporate_action_dir()),
                ]
            )
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

    def _stage_compute_experiment(self) -> tuple[dict[str, Any], dict[str, str]]:
        experiment_dir = Path(self.config.experiment_output_dir) if self.config.experiment_output_dir else self.output_dir / "experiment"
        compute_state_dir = Path(self.config.compute_state_dir) if self.config.compute_state_dir else self.output_dir / "compute_state"
        corpus_path = self._formula_corpus_dir() / "formula_corpus.jsonl"
        if not corpus_path.exists() and self.config.build_formula_corpus:
            self._stage_formula_corpus()
        report = run_workflow_smoke(
            {
                "workflow": self.config.experiment_workflow,
                "data_dir": self.config.data_dir,
                "data_freeze_dir": self.config.data_freeze_dir,
                "data_freeze_id": self.data_freeze_id,
                "data_version_manifest_path": self.config.data_version_manifest_path,
                "require_data_freeze": self.config.require_data_freeze,
                "factor_store_dir": self.config.factor_store_dir,
                "matrix_cache_dir": str(self._matrix_cache_dir()) if self.config.use_matrix_cache or self.config.matrix_cache_dir else None,
                "formula_corpus_path": str(corpus_path) if corpus_path.exists() else None,
                "output_dir": str(experiment_dir),
                "compute_state_dir": str(compute_state_dir),
                "gpu_count": self.config.gpu_count,
                "shard_count": self.config.shard_count,
                "max_formulas": self.config.search_max_candidates,
                "device": "cuda" if self.config.gpu_count > 0 else "cpu",
                "use_ddp_pretrain": self.config.use_ddp_pretrain,
                "pretrain_epochs": self.config.pretrain_epochs,
                "pretrain_batch_size": self.config.pretrain_batch_size,
                "search_mode": self.config.search_mode,
                "search_generations": self.config.search_generations,
                "search_population_size": self.config.search_population_size,
                "search_max_candidates": self.config.search_max_candidates,
                "batch_eval_chunk_size": self.config.batch_eval_chunk_size,
                "max_parallel_gpu_jobs": self.config.max_parallel_gpu_jobs,
                "max_parallel_cpu_jobs": self.config.max_parallel_cpu_jobs,
                "resume": self.config.resume_compute,
                "dry_run": self.config.compute_dry_run,
            }
        )
        payload = report.to_dict()
        summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
        self.compute_summary = {
            "run_id": summary.get("compute_run_id") or Path(str(payload.get("compute_run_report_path") or "")).stem,
            "experiment_id": payload.get("experiment_id"),
            "gpu_count_detected": summary.get("gpu_count_detected", 0),
            "gpu_count_used": self.config.gpu_count,
            "shard_count": payload.get("shard_count", self.config.shard_count),
            "compute_success_count": summary.get("compute_success_count", 0),
            "compute_failed_count": summary.get("compute_failed_count", 0),
            "compute_resumed_count": summary.get("compute_resumed_count", 0),
            "total_gpu_allocated_seconds": summary.get("total_gpu_allocated_seconds", 0.0),
            "formula_eval_throughput": summary.get("formula_eval_throughput", 0.0),
            "pretrain_samples_per_second": summary.get("pretrain_samples_per_second", 0.0),
            "fallback_to_cpu_count": summary.get("fallback_to_cpu_count", 0),
            "cuda_oom_count": summary.get("cuda_oom_count", 0),
            "experiment_plan_path": payload.get("plan_path"),
            "compute_run_report_path": payload.get("compute_run_report_path"),
            "experiment_merge_report_path": payload.get("merge_report_path"),
        }
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        for name, path in paths.items():
            if path:
                self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "compute_experiment")
        return self.compute_summary | {"status": payload.get("status")}, {str(k): str(v) for k, v in paths.items() if v}

    def _stage_alpha_factory(self) -> tuple[dict[str, Any], dict[str, str]]:
        alpha_dir = Path(self.config.alpha_factory_dir) if self.config.alpha_factory_dir else self.output_dir / "alpha_factory"
        feature_dir = (
            Path(self.config.alpha_feature_output_dir)
            if self.config.alpha_feature_output_dir
            else alpha_dir / "features"
        )
        corpus_path = self._formula_corpus_dir() / "formula_corpus.jsonl"
        if self.config.alpha_corpus_budget > 0 and not corpus_path.exists():
            self._stage_formula_corpus()
        argv = [
            "run",
            "--campaign-name",
            self.config.alpha_campaign_name,
            "--data-dir",
            self.config.data_dir,
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--output-dir",
            str(alpha_dir),
            "--report-dir",
            self.config.report_dir,
            "--feature-set-name",
            self.config.alpha_feature_set_name,
            "--candidate-budget",
            str(self.config.alpha_candidate_budget),
            "--template-budget",
            str(self.config.alpha_template_budget),
            "--random-budget",
            str(self.config.alpha_random_budget),
            "--mutation-budget",
            str(self.config.alpha_mutation_budget),
            "--crossover-budget",
            str(self.config.alpha_crossover_budget),
            "--corpus-budget",
            str(self.config.alpha_corpus_budget),
            "--neural-budget",
            str(self.config.alpha_neural_budget),
            "--proxy-max-candidates",
            str(max(self.config.alpha_candidate_budget, 1)),
            "--top-k",
            str(self.config.alpha_top_k),
            "--max-per-family",
            str(self.config.alpha_max_per_family),
            "--min-novelty-score",
            str(self.config.alpha_min_novelty_score),
            "--factor-transform",
            self.config.factor_transform,
            "--universe-name",
            self.config.universe_name,
        ]
        argv.extend(_freeze_cli_args(self.config))
        if self.config.alpha_build_feature_set:
            argv.extend(["--build-feature-set", "--feature-output-dir", str(feature_dir)])
        if corpus_path.exists():
            argv.extend(["--formula-corpus-path", str(corpus_path)])
        if self.config.use_matrix_cache:
            argv.extend(["--matrix-cache-dir", str(self._matrix_cache_dir())])
        if self.config.alpha_use_batch_eval:
            argv.extend(
                [
                    "--use-batch-eval",
                    "--batch-eval-dir",
                    str(alpha_dir / "batch_eval"),
                    "--batch-eval-chunk-size",
                    str(self.config.batch_eval_chunk_size),
                    "--batch-eval-device",
                    self.config.batch_eval_device,
                    "--enable-gate",
                    "--correlation-threshold",
                    "0.99",
                    "--min-coverage",
                    "0.5",
                ]
            )
        if self.config.use_eval_cache:
            argv.append("--use-eval-cache")
            if self.config.eval_cache_dir:
                argv.extend(["--eval-cache-dir", self.config.eval_cache_dir])
        if self.config.alpha_use_compute_scheduler:
            argv.extend(
                [
                    "--use-compute-scheduler",
                    "--compute-state-dir",
                    str(self.config.compute_state_dir or self.output_dir / "alpha_compute_state"),
                    "--compute-output-dir",
                    str(self.config.compute_output_dir or self.output_dir / "alpha_compute"),
                    "--shard-count",
                    str(self.config.alpha_shard_count),
                    "--max-parallel-cpu-jobs",
                    str(self.config.max_parallel_cpu_jobs),
                    "--max-parallel-gpu-jobs",
                    str(self.config.max_parallel_gpu_jobs),
                ]
            )
        if self.config.alpha_register_shortlist:
            argv.append("--register-shortlist")
        if self.config.point_in_time:
            argv.extend(["--point-in-time", "--feature-cutoff-mode", self.config.feature_cutoff_mode])
        if self.config.corporate_action_aware:
            argv.extend(["--corporate-action-aware", "--target-return-mode", self.config.target_return_mode])
        payload = _run_json_main(run_alpha_factory_main, argv)
        summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        self.alpha_summary = dict(summary) | {
            "campaign_id": payload.get("campaign_id"),
            "alpha_factory_report_path": paths.get("alpha_factory_report_path"),
            "alpha_campaign_manifest_path": paths.get("alpha_campaign_manifest_path"),
            "alpha_shortlist_path": paths.get("alpha_shortlist_path"),
            "feature_set_manifest_path": paths.get("feature_set_manifest_path"),
        }
        output_paths = {name.replace("_path", ""): str(path) for name, path in paths.items() if path}
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "alpha_factory")
        return self.alpha_summary, output_paths

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
            + _freeze_cli_args(self.config)
            + (["--corpus-sequence-path", str(self._formula_corpus_dir() / "formula_sequences.jsonl")] if (self._formula_corpus_dir() / "formula_sequences.jsonl").exists() else [])
            + (
                [
                    "--alpha-candidates-path",
                    str((Path(self.config.alpha_factory_dir) if self.config.alpha_factory_dir else self.output_dir / "alpha_factory") / "alpha_shortlist.jsonl"),
                    "--alpha-campaign-manifest-path",
                    str((Path(self.config.alpha_factory_dir) if self.config.alpha_factory_dir else self.output_dir / "alpha_factory") / "alpha_campaign_manifest.json"),
                    "--use-alpha-shortlist-as-seed",
                    "--alpha-seed-top-k",
                    str(self.config.alpha_top_k),
                    "--feature-set-name",
                    self.config.alpha_feature_set_name,
                ]
                + (
                    [
                        "--feature-set-manifest-path",
                        str((Path(self.config.alpha_feature_output_dir) if self.config.alpha_feature_output_dir else (Path(self.config.alpha_factory_dir) if self.config.alpha_factory_dir else self.output_dir / "alpha_factory") / "features") / "feature_set_manifest.json"),
                    ]
                    if self.config.alpha_build_feature_set
                    else []
                )
                if self.config.use_alpha_shortlist_for_search
                else []
            )
            + (["--matrix-cache-dir", str(self._matrix_cache_dir()), "--use-matrix-cache"] if self.config.use_matrix_cache else [])
            + (
                [
                    "--use-compute-scheduler",
                    "--compute-state-dir",
                    str(self.config.compute_state_dir or self.output_dir / "compute_state"),
                    "--compute-output-dir",
                    str(self.config.compute_output_dir or self.output_dir / "compute"),
                    "--formula-shard-count",
                    str(self.config.formula_shards),
                    "--experiment-id",
                    str(self.compute_summary.get("experiment_id") or self.config.suite_name),
                ]
                if self.config.use_compute_scheduler
                else []
            )
            + (
                [
                    "--corporate-action-aware",
                    "--target-return-mode",
                    self.config.target_return_mode,
                    "--corporate-action-dir",
                    str(self._corporate_action_dir()),
                    "--corporate-action-cash-field",
                    self.config.corporate_action_cash_field,
                ]
                if self.config.corporate_action_aware
                else []
            )
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
        argv.extend(_freeze_cli_args(self.config))
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
        if self.config.corporate_action_aware:
            argv.extend(
                [
                    "--corporate-action-aware",
                    "--target-return-mode",
                    self.config.target_return_mode,
                    "--corporate-action-dir",
                    str(self._corporate_action_dir()),
                    "--corporate-action-report-dir",
                    str(self._corporate_action_dir()),
                    "--corporate-action-cash-field",
                    self.config.corporate_action_cash_field,
                    "--corporate-action-application-date-mode",
                    self.config.corporate_action_application_date_mode,
                ]
            )
            if self.config.reconcile_adjustment_factors:
                argv.append("--reconcile-adjustment-factors")
        if self.config.run_leakage_audit:
            argv.extend(["--run-leakage-audit", "--leakage-audit-dir", str(self._backtest_leakage_dir())])
            if self.config.fail_on_leakage_blocker:
                argv.append("--fail-on-leakage-blocker")
        if self.config.settlement_aware:
            argv.extend(
                [
                    "--settlement-aware",
                    "--settlement-dir",
                    str(self._settlement_dir("backtest")),
                    "--settlement-profile",
                    self.config.settlement_profile,
                    "--cost-basis-method",
                    self.config.cost_basis_method,
                    "--write-settlement-report",
                ]
            )

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
        if payload.get("corporate_action_report_path"):
            output_paths["backtest_corporate_action_report"] = str(payload["corporate_action_report_path"])
        if payload.get("total_return_report_path"):
            output_paths["backtest_total_return_report"] = str(payload["total_return_report_path"])
        if payload.get("adjustment_reconciliation_path"):
            output_paths["backtest_adjustment_reconciliation"] = str(payload["adjustment_reconciliation_path"])
        for key in (
            "settlement_report_path",
            "settlement_events_path",
            "cash_buckets_path",
            "position_lots_path",
            "position_availability_path",
            "realized_pnl_path",
            "account_nav_path",
            "account_performance_report_path",
            "account_reconciliation_report_path",
            "fee_tax_report_path",
        ):
            if payload.get(key):
                output_paths[f"backtest_{key.replace('_path', '')}"] = str(payload[key])
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
        argv.extend(_freeze_cli_args(self.config))
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
        if self.config.corporate_action_aware:
            argv.extend(
                [
                    "--corporate-action-aware",
                    "--target-return-mode",
                    self.config.target_return_mode,
                    "--corporate-action-dir",
                    str(self._corporate_action_dir()),
                    "--corporate-action-cash-field",
                    self.config.corporate_action_cash_field,
                ]
            )
        if self.config.settlement_aware:
            argv.extend(
                [
                    "--settlement-aware",
                    "--settlement-dir",
                    str(self._settlement_dir("orders")),
                    "--settlement-profile",
                    self.config.settlement_profile,
                    "--paper-account-dir",
                    str(self._settlement_dir("orders") / "account"),
                ]
            )

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
        if payload.get("corporate_action_report_path"):
            output_paths["orders_corporate_action_report"] = str(payload["corporate_action_report_path"])
        settlement_precheck = payload.get("settlement_precheck", {}) if isinstance(payload.get("settlement_precheck"), dict) else {}
        paths = settlement_precheck.get("settlement_report_paths", {}) if isinstance(settlement_precheck.get("settlement_report_paths"), dict) else {}
        for key, path in paths.items():
            output_paths[f"orders_{key.replace('_path', '')}"] = str(path)
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
            corporate_action_aware=self.config.corporate_action_aware,
            corporate_action_dir=str(self._corporate_action_dir()),
            target_return_mode=self.config.target_return_mode,
            corporate_action_cash_field=self.config.corporate_action_cash_field,
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

    def _stage_validation_lab(self) -> tuple[dict[str, Any], dict[str, str]]:
        if not self.selected_factor_id:
            raise ValueError("no selected factor for validation lab")
        validation_dir = self._validation_lab_dir()
        alpha_dir = Path(self.config.alpha_factory_dir) if self.config.alpha_factory_dir else self.output_dir / "alpha_factory"
        search_dir = Path(self.config.output_dir) / "search"
        batch_dir = self._batch_eval_dir()
        argv = [
            "run-suite",
            "--data-dir",
            self.config.data_dir,
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--factor-id",
            self.selected_factor_id,
            "--factor-type",
            "composite",
            "--output-dir",
            str(validation_dir),
            "--as-of-date",
            self.config.as_of_date,
            "--universe-name",
            self.config.universe_name,
            "--split-method",
            self.config.validation_split_method,
            "--train-size",
            str(self.config.validation_train_size),
            "--validation-size",
            str(self.config.validation_size),
            "--test-size",
            str(self.config.validation_test_size),
            "--step-size",
            str(self.config.validation_step_size),
            "--embargo-size",
            str(self.config.validation_embargo_size),
            "--cscv-groups",
            str(self.config.validation_cscv_groups),
            "--max-cscv-combinations",
            str(self.config.validation_max_cscv_combinations),
            "--placebo-trials",
            str(self.config.placebo_trials),
            "--alpha-factory-report-path",
            str(alpha_dir / "alpha_factory_report.json"),
            "--alpha-candidates-path",
            str(alpha_dir / "alpha_candidates.jsonl"),
            "--alpha-shortlist-path",
            str(alpha_dir / "alpha_shortlist.jsonl"),
            "--alpha-full-eval-summary-path",
            str(alpha_dir / "alpha_full_eval_summary.json"),
            "--formula-search-result-path",
            str(search_dir / "search_result.json"),
            "--batch-eval-result-path",
            str(batch_dir / "formula_batch_eval_result.json"),
        ]
        argv.extend(_freeze_cli_args(self.config))
        if self.config.run_multiple_testing:
            argv.append("--run-multiple-testing")
        if self.config.run_overfit_risk:
            argv.append("--run-overfit-risk")
        if self.config.run_placebo:
            argv.append("--run-placebo")
        if self.config.run_regime_validation:
            argv.append("--run-regime")
        if self.config.run_sensitivity_validation:
            argv.append("--run-sensitivity")
        if self.config.run_stress_backtest_validation:
            argv.append("--run-stress-backtest")
        payload = _run_json_main(run_validation_main, argv)
        self.validation_summary = payload
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        output_paths = {name.replace("_path", ""): str(path) for name, path in paths.items() if path}
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "validation_lab")
        return payload, output_paths

    def _stage_factor_certification(self) -> tuple[dict[str, Any], dict[str, str]]:
        if not self.selected_factor_id:
            raise ValueError("no selected factor for certification")
        cert_dir = self._factor_certification_dir()
        validation_dir = self._validation_lab_dir()
        argv = [
            "run",
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--factor-id",
            self.selected_factor_id,
            "--factor-type",
            "composite",
            "--output-dir",
            str(cert_dir),
            "--policy-profile",
            self.config.certification_policy_profile,
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
            "--data-version-manifest-path",
            self.config.data_version_manifest_path or "",
            "--research-data-freeze-path",
            str(Path(self.config.data_freeze_dir) / "research_data_freeze.json") if self.config.data_freeze_dir else "",
            "--alpha-factory-report-path",
            str((Path(self.config.alpha_factory_dir) if self.config.alpha_factory_dir else self.output_dir / "alpha_factory") / "alpha_factory_report.json"),
        ]
        if self.config.certification_policy_path:
            argv.extend(["--policy-path", self.config.certification_policy_path])
        if self.config.fail_on_certification_rejected:
            argv.append("--fail-on-rejected")
        payload = _run_json_main(run_certify_main, argv)
        self.certification_summary = payload
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        output_paths = {name.replace("_path", ""): str(path) for name, path in paths.items() if path}
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "factor_certification")
        return payload, output_paths

    def _stage_portfolio_lab(self) -> tuple[dict[str, Any], dict[str, str]]:
        if not self.selected_factor_id:
            raise ValueError("no selected factor for portfolio lab")
        lab_dir = self._portfolio_lab_dir()
        argv = [
            "run",
            "--data-dir",
            self.config.data_dir,
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--factor-id",
            self.selected_factor_id,
            "--factor-type",
            "composite",
            "--output-dir",
            str(lab_dir),
            "--index-code",
            self.config.index_code,
            "--scenario-profile",
            self.config.portfolio_lab_scenario_profile,
            "--portfolio-methods",
            self.config.portfolio_methods,
            "--risk-aversions",
            self.config.portfolio_risk_aversions,
            "--turnover-penalties",
            self.config.portfolio_turnover_penalties,
            "--benchmark-weights",
            self.config.portfolio_benchmark_weights,
            "--max-weight-values",
            self.config.portfolio_max_weight_values,
            "--max-names-values",
            self.config.portfolio_max_names_values,
            "--max-turnover-values",
            self.config.portfolio_max_turnover_values,
            "--max-tracking-error-values",
            self.config.portfolio_max_tracking_error_values,
            "--top-n-values",
            self.config.portfolio_top_n_values,
        ]
        if self.config.portfolio_policy_grid_path:
            argv.extend(["--policy-grid-path", self.config.portfolio_policy_grid_path])
        if self.config.use_factor_risk_model:
            argv.append("--use-factor-risk-model")
        payload = _run_json_main(run_portfolio_lab_main, argv)
        self.portfolio_lab_summary = payload
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        output_paths = {name.replace("_path", ""): str(path) for name, path in paths.items() if path}
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "portfolio_lab")
        return payload, output_paths

    def _stage_portfolio_certification(self) -> tuple[dict[str, Any], dict[str, str]]:
        if not self.selected_factor_id:
            raise ValueError("no selected factor for portfolio certification")
        cert_dir = self._portfolio_certification_dir()
        lab_dir = self._portfolio_lab_dir()
        factor_cert_dir = self._factor_certification_dir()
        argv = [
            "run",
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--factor-id",
            self.selected_factor_id,
            "--factor-type",
            "composite",
            "--output-dir",
            str(cert_dir),
            "--portfolio-policy-path",
            str(lab_dir / "selected_portfolio_policy.json"),
            "--portfolio-lab-report-path",
            str(lab_dir / "portfolio_lab_report.json"),
            "--portfolio-robustness-report-path",
            str(lab_dir / "portfolio_robustness_report.json"),
            "--factor-certification-decision-path",
            str(factor_cert_dir / "factor_certification_decision.json"),
            "--policy-profile",
            self.config.portfolio_certification_policy_profile,
        ]
        if self.config.portfolio_certification_policy_path:
            argv.extend(["--policy-path", self.config.portfolio_certification_policy_path])
        if self.config.register_optimizer_policy:
            argv.extend(["--register-policy", "--model-registry-dir", str(self._model_registry_dir())])
        if self.config.create_portfolio_policy_approval:
            argv.extend(["--create-activation-approval", "--approval-store-dir", str(self._portfolio_policy_approval_store_dir())])
        if self.config.fail_on_portfolio_certification_rejected:
            argv.append("--fail-on-rejected")
        payload = _run_json_main(run_portfolio_certify_main, argv)
        self.portfolio_certification_summary = payload
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        output_paths = {name.replace("_path", ""): str(path) for name, path in paths.items() if path}
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "portfolio_certification")
        return payload, output_paths

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
        if self.config.require_certification:
            status = str(self.certification_summary.get("certification_status") or "")
            if status not in {"certified", "conditional"}:
                from .models import PromotionDecision

                self.promotion_decision = PromotionDecision(
                    factor_id=self.selected_factor_id,
                    passed=False,
                    new_status="needs_review",
                    reasons=[f"certification_not_passed:{status or 'missing'}"],
                    checks={"certification_status": status, "require_certification": True},
                    created_at=_utc_now(),
                )
                path = write_promotion_decision(self.promotion_decision, self.config.output_dir)
                self.catalog = register_artifact(self.catalog, "promotion_decision", path, "json", "promotion")
                return self.promotion_decision.to_dict(), {"promotion_decision": str(path)}
        if self.config.require_portfolio_certification:
            status = str(self.portfolio_certification_summary.get("certification_status") or "")
            if status not in {"certified", "conditional"}:
                from .models import PromotionDecision

                self.promotion_decision = PromotionDecision(
                    factor_id=self.selected_factor_id,
                    passed=False,
                    new_status="needs_review",
                    reasons=[f"portfolio_certification_not_passed:{status or 'missing'}"],
                    checks={"portfolio_certification_status": status, "require_portfolio_certification": True},
                    created_at=_utc_now(),
                )
                path = write_promotion_decision(self.promotion_decision, self.config.output_dir)
                self.catalog = register_artifact(self.catalog, "promotion_decision", path, "json", "promotion")
                return self.promotion_decision.to_dict(), {"promotion_decision": str(path)}
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
                certification_status=self.certification_summary.get("certification_status"),
                require_certification=self.config.require_certification,
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
                "corporate_action_summary": self.corporate_action_summary,
                "corporate_action_aware": self.config.corporate_action_aware,
                "target_return_mode": self.config.target_return_mode,
                "alpha_campaign_id": self.alpha_summary.get("campaign_id"),
                "alpha_factory_report_path": self.alpha_summary.get("alpha_factory_report_path"),
                "alpha_campaign_manifest_path": self.alpha_summary.get("alpha_campaign_manifest_path"),
                "alpha_shortlist_path": self.alpha_summary.get("alpha_shortlist_path"),
                "validation_lab_summary": self.validation_summary,
                "factor_certification_summary": self.certification_summary,
                "feature_set_name": self.alpha_summary.get("feature_set_name"),
                "feature_version": self.alpha_summary.get("feature_set_name"),
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
        ca_report = self._corporate_action_dir() / "corporate_actions_report.json"
        tr_report = self._corporate_action_dir() / "total_return_report.json"
        ca_validation = self._corporate_action_dir() / "corporate_action_validation_report.json"
        ca_reconciliation = self._corporate_action_dir() / "adjustment_factor_reconciliation.json"
        settlement_dir = self._settlement_dir("backtest")
        settlement_report = settlement_dir / "settlement_report.json"
        account_reconciliation = settlement_dir / "account_reconciliation_report.json"
        account_performance = settlement_dir / "account_performance_report.json"
        cash_buckets = settlement_dir / "cash_buckets.jsonl"
        realized_pnl = settlement_dir / "realized_pnl.jsonl"
        if pit_report.exists():
            argv.extend(["--pit-validation-report-path", str(pit_report)])
        if survivorship_report.exists():
            argv.extend(["--survivorship-report-path", str(survivorship_report)])
        if leakage_report.exists():
            argv.extend(["--leakage-audit-report-path", str(leakage_report)])
        if truncation_report.exists():
            argv.extend(["--truncation-consistency-report-path", str(truncation_report)])
        if ca_report.exists():
            argv.extend(["--corporate-action-report-path", str(ca_report)])
        if tr_report.exists():
            argv.extend(["--total-return-report-path", str(tr_report)])
        if ca_validation.exists():
            argv.extend(["--corporate-action-validation-path", str(ca_validation)])
        if ca_reconciliation.exists():
            argv.extend(["--adjustment-reconciliation-path", str(ca_reconciliation)])
        if settlement_report.exists():
            argv.extend(["--settlement-report-path", str(settlement_report)])
        if account_reconciliation.exists():
            argv.extend(["--account-reconciliation-report-path", str(account_reconciliation)])
        if account_performance.exists():
            argv.extend(["--account-performance-report-path", str(account_performance)])
        if cash_buckets.exists():
            argv.extend(["--cash-buckets-path", str(cash_buckets)])
        if realized_pnl.exists():
            argv.extend(["--realized-pnl-path", str(realized_pnl)])
        validation_dir = self._validation_lab_dir()
        cert_dir = self._factor_certification_dir()
        cert_decision = cert_dir / "factor_certification_decision.json"
        if cert_decision.exists():
            argv.extend(["--artifact-dir", str(cert_dir)])
        if (validation_dir / "validation_lab_report.json").exists():
            argv.extend(["--artifact-dir", str(validation_dir)])
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

    def _data_lake_registry_dir(self) -> Path:
        return Path(self.config.data_lake_registry_dir) if self.config.data_lake_registry_dir else Path(self.config.output_dir).parent / "data_lake_registry"

    def _data_version_dir(self) -> Path:
        return Path(self.config.output_dir) / "data_version"

    def _freeze_validation_dir(self) -> Path:
        return Path(self.config.output_dir) / "freeze_validation"

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

    def _settlement_dir(self, stage: str) -> Path:
        base = Path(self.config.settlement_dir) if self.config.settlement_dir else self.output_dir.parent / "settlement"
        return base / stage

    def _corporate_action_dir(self) -> Path:
        if self.config.corporate_action_dir:
            return Path(self.config.corporate_action_dir)
        if self.config.corporate_action_output_dir:
            return Path(self.config.corporate_action_output_dir)
        return Path(self.config.output_dir) / "corporate_actions"

    def _validation_lab_dir(self) -> Path:
        return Path(self.config.validation_lab_dir) if self.config.validation_lab_dir else Path(self.config.output_dir) / "validation_lab"

    def _factor_certification_dir(self) -> Path:
        return Path(self.config.factor_certification_dir) if self.config.factor_certification_dir else Path(self.config.output_dir) / "factor_certification"

    def _portfolio_lab_dir(self) -> Path:
        return Path(self.config.portfolio_lab_dir) if self.config.portfolio_lab_dir else Path(self.config.output_dir) / "portfolio_lab"

    def _portfolio_certification_dir(self) -> Path:
        return (
            Path(self.config.portfolio_certification_dir)
            if self.config.portfolio_certification_dir
            else Path(self.config.output_dir) / "portfolio_certification"
        )

    def _portfolio_policy_approval_store_dir(self) -> Path:
        return (
            Path(self.config.portfolio_policy_approval_store_dir)
            if self.config.portfolio_policy_approval_store_dir
            else Path(self.config.output_dir).parent / "approvals"
        )


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


def _dataset_version_id(path: str | None) -> str | None:
    if not path:
        return None
    payload = _read_json(Path(path))
    value = payload.get("dataset_version_id")
    return str(value) if value else None


def _quality_error_count(path: Path) -> int:
    payload = _read_json(path)
    return int(payload.get("total_errors", 0) or 0) if payload else 0


def _freeze_cli_args(config: ResearchSuiteConfig) -> list[str]:
    argv: list[str] = []
    if config.data_freeze_dir:
        argv.extend(["--data-freeze-dir", config.data_freeze_dir])
    if config.data_freeze_id:
        argv.extend(["--data-freeze-id", config.data_freeze_id])
    if config.data_version_manifest_path:
        argv.extend(["--data-version-manifest-path", config.data_version_manifest_path])
    if config.freeze_validation_report_path:
        argv.extend(["--freeze-validation-report-path", config.freeze_validation_report_path])
    if config.require_data_freeze:
        argv.append("--require-data-freeze")
    return argv


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _artifact_kind(path: str) -> str:
    if path.endswith(".md"):
        return "markdown"
    if path.endswith(".jsonl"):
        return "jsonl"
    return "json"
