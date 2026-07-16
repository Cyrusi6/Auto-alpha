"""Local artifact reader for the A-share dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DashboardConfig


class AshareDashboardService:
    def __init__(self, config: DashboardConfig | None = None):
        self.config = config or DashboardConfig.from_env()

    def load_manifest(self) -> dict[str, Any]:
        return self._read_json(self.config.data_dir / "manifest.json")

    def load_quality_report(self) -> dict[str, Any]:
        return self._read_json(self.config.data_dir / "quality_report.json")

    def load_sync_plan(self) -> dict[str, Any]:
        return self._read_json(self.config.data_dir / "sync_plan.json")

    def load_pipeline_state(self) -> dict[str, Any]:
        return self._read_json(self.config.data_dir / "pipeline_state.json")

    def load_api_audit(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.data_dir / "api_audit.jsonl")

    def load_dataset_stats(self) -> dict[str, Any]:
        return self._read_json(self.config.data_dir / "dataset_stats.json")

    def load_real_data_profile(self) -> dict[str, Any]:
        return self._read_first_json(self._real_data_artifact_candidates("real_data_profile.json"))

    def load_real_data_readiness_report(self) -> dict[str, Any]:
        return self._read_first_json(self._real_data_artifact_candidates("real_data_readiness_report.json"))

    def load_real_data_pipeline_report(self) -> dict[str, Any]:
        return self._read_first_json(self._real_data_artifact_candidates("real_data_pipeline_report.json"))

    def load_real_data_runbook(self) -> dict[str, Any]:
        return self._read_first_json(self._real_data_artifact_candidates("real_data_runbook.json"))

    def load_real_data_sla_report(self) -> dict[str, Any]:
        return self._read_first_json(self._real_data_artifact_candidates("real_data_sla_report.json"))

    def load_real_data_size_report(self) -> dict[str, Any]:
        return self._read_first_json(self._real_data_artifact_candidates("real_data_size_report.json"))

    def load_provider_readiness_matrix(self) -> dict[str, Any]:
        return self._read_first_json(self._real_data_artifact_candidates("provider_readiness_matrix.json"))

    def load_api_permission_matrix(self) -> dict[str, Any]:
        return self._read_first_json(self._real_data_artifact_candidates("api_permission_matrix.json"))

    def load_required_dataset_status(self) -> dict[str, Any]:
        return self._read_first_json(self._real_data_artifact_candidates("required_dataset_status.json"))

    def load_matrix_refresh_result(self) -> dict[str, Any]:
        return self._read_first_json(self._matrix_refresh_artifact_candidates("matrix_refresh_result.json"))

    def load_matrix_freshness_report(self) -> dict[str, Any]:
        return self._read_first_json(self._matrix_refresh_artifact_candidates("matrix_freshness_report.json"))

    def load_snapshot_summary(self) -> pd.DataFrame:
        snapshots_dir = self.config.data_dir / "snapshots"
        records: list[dict[str, Any]] = []
        if not snapshots_dir.exists():
            return pd.DataFrame()
        for snapshot_dir in sorted(path for path in snapshots_dir.iterdir() if path.is_dir()):
            datasets = sorted(
                dataset_dir.name
                for dataset_dir in snapshot_dir.iterdir()
                if (dataset_dir / "records.jsonl").exists()
            )
            records.append(
                {
                    "snapshot": snapshot_dir.name,
                    "datasets": len(datasets),
                    "dataset_names": ", ".join(datasets),
                }
            )
        return pd.DataFrame(records)

    def load_dataset(self, name: str, limit: int | None = 200) -> pd.DataFrame:
        frame = self._read_jsonl(self.config.data_dir / name / "records.jsonl")
        if limit is not None and len(frame) > limit:
            return frame.head(limit)
        return frame

    def load_factors(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.factor_store_dir / "factors.jsonl")

    def load_factor_overview(self) -> pd.DataFrame:
        factors = self.load_factors()
        if factors.empty:
            return factors

        records: list[dict[str, Any]] = []
        for _, row in factors.iterrows():
            metadata = row.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            similar = metadata.get("similar_factors")
            similar = similar if isinstance(similar, list) else []
            components = row.get("parent_factor_ids") or metadata.get("component_factor_ids") or []
            components = components if isinstance(components, list) else []
            gate_reasons = row.get("gate_reasons")
            gate_reasons = gate_reasons if isinstance(gate_reasons, list) else []
            records.append(
                {
                    "factor_id": row.get("factor_id"),
                    "factor_type": row.get("factor_type") or "single",
                    "batch_id": row.get("batch_id") or metadata.get("batch_id") or "",
                    "component_factor_ids": ", ".join(str(item) for item in components),
                    "formula_complexity": metadata.get("formula_complexity", ""),
                    "formula_lookback": metadata.get("formula_lookback", ""),
                    "formula_source": metadata.get("formula_source", ""),
                    "generation": metadata.get("generation", ""),
                    "status": row.get("status") or "candidate",
                    "transform_method": row.get("transform_method") or "raw",
                    "gate_status": row.get("gate_status") or "",
                    "gate_reasons": ", ".join(str(reason) for reason in gate_reasons),
                    "max_abs_correlation": float(metadata.get("max_abs_correlation", 0.0) or 0.0),
                    "similar_factors": len(similar),
                    "score": self._metric_value(row.get("metrics"), "score"),
                }
            )
        return pd.DataFrame(records)

    def load_experiments(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.factor_store_dir / "experiments.jsonl")

    def load_latest_factor_metrics(self) -> dict[str, Any]:
        factors = self.load_factors()
        if factors.empty:
            return {}
        latest = factors.iloc[-1].to_dict()
        metrics = latest.get("metrics")
        return metrics if isinstance(metrics, dict) else {}

    @staticmethod
    def _metric_value(metrics: Any, key: str) -> float:
        if isinstance(metrics, dict):
            return float(metrics.get(key, 0.0) or 0.0)
        return 0.0

    def load_factor_report_json(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir / "factor_report.json")

    def load_factor_report_markdown(self) -> str:
        path = self.config.report_dir / "factor_report.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def load_batch_report_json(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir / "batch_report.json") or self._read_json(
            self.config.report_dir.parent / "batch" / "batch_report.json"
        )

    def load_batch_report_markdown(self) -> str:
        candidates = [
            self.config.report_dir / "batch_report.md",
            self.config.report_dir.parent / "batch" / "batch_report.md",
        ]
        for path in candidates:
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_search_report_json(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir / "search_report.json") or self._read_json(
            self.config.report_dir.parent / "search" / "search_report.json"
        )

    def load_search_report_markdown(self) -> str:
        candidates = [
            self.config.report_dir / "search_report.md",
            self.config.report_dir.parent / "search" / "search_report.md",
        ]
        for path in candidates:
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_neural_search_result(self) -> dict[str, Any]:
        for path in self._neural_artifact_candidates("neural_search_result.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_neural_training_history(self) -> pd.DataFrame:
        for path in self._neural_artifact_candidates("neural_training_history.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_neural_search_report_markdown(self) -> str:
        for path in self._neural_artifact_candidates("neural_search_report.md"):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_neural_checkpoints(self) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        for directory in self._neural_checkpoint_candidates():
            if not directory.exists() or not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.pt")):
                records.append({"path": str(path), "name": path.name, "size_bytes": path.stat().st_size})
        return pd.DataFrame(records)

    def load_suite_result(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir.parent / "suite" / "suite_result.json")

    def load_suite_report_markdown(self) -> str:
        path = self.config.report_dir.parent / "suite" / "suite_report.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def load_artifact_catalog(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir.parent / "suite" / "artifact_catalog.json")

    def load_promotion_decision(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir.parent / "suite" / "promotion_decision.json")

    def load_risk_report_json(self) -> dict[str, Any]:
        for path in [
            self.config.backtest_dir / "risk" / "risk_model_report.json",
            self.config.backtest_dir / "risk" / "risk_report.json",
            self.config.backtest_dir / "risk_model_report.json",
            self.config.backtest_dir / "risk_report.json",
            self.config.orders_dir / "risk_model_report.json",
            self.config.orders_dir / "risk_report.json",
        ]:
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_risk_report_markdown(self) -> str:
        for path in [
            self.config.backtest_dir / "risk" / "risk_model_report.md",
            self.config.backtest_dir / "risk" / "risk_report.md",
            self.config.backtest_dir / "risk_model_report.md",
            self.config.backtest_dir / "risk_report.md",
            self.config.orders_dir / "risk_model_report.md",
            self.config.orders_dir / "risk_report.md",
        ]:
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_risk_exposures(self) -> pd.DataFrame:
        for path in self._risk_artifact_candidates("risk_exposures.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_risk_decomposition(self) -> pd.DataFrame:
        for path in self._risk_artifact_candidates("risk_decomposition.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_return_attribution(self) -> pd.DataFrame:
        for path in self._risk_artifact_candidates("return_attribution.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_optimization_result(self) -> dict[str, Any]:
        for path in [
            self.config.backtest_dir / "optimization_result.json",
            self.config.orders_dir / "optimization_result.json",
            self.config.report_dir.parent / "optimize" / "optimization_result.json",
        ]:
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_feature_set_manifest(self) -> dict[str, Any]:
        return self._read_first_json(self._feature_artifact_candidates("feature_set_manifest.json"))

    def load_feature_coverage_report(self) -> dict[str, Any]:
        return self._read_first_json(self._feature_artifact_candidates("feature_coverage_report.json"))

    def load_feature_values_summary(self) -> dict[str, Any]:
        return self._read_first_json(self._feature_artifact_candidates("feature_values_summary.json"))

    def load_feature_family_readiness(self) -> dict[str, Any]:
        return self._read_first_json(self._feature_artifact_candidates("feature_family_readiness.json"))

    def load_feature_pit_alignment_report(self) -> dict[str, Any]:
        return self._read_first_json(self._feature_artifact_candidates("feature_pit_alignment_report.json"))

    def load_feature_build_warnings(self) -> pd.DataFrame:
        for path in self._feature_artifact_candidates("feature_build_warnings.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_feature_promotion_policy(self) -> dict[str, Any]:
        return self._read_first_json(self._feature_promotion_artifact_candidates("feature_promotion_policy.json"))

    def load_feature_promotion_evidence_report(self) -> dict[str, Any]:
        return self._read_first_json(self._feature_promotion_artifact_candidates("feature_promotion_evidence_report.json"))

    def load_feature_promotion_review_package(self) -> dict[str, Any]:
        return self._read_first_json(self._feature_promotion_artifact_candidates("feature_promotion_review_package.json"))

    def load_feature_promotion_decisions(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._feature_promotion_artifact_candidates("feature_promotion_decisions.jsonl"))

    def load_feature_promotion_allowlist(self) -> dict[str, Any]:
        return self._read_first_json(self._feature_promotion_artifact_candidates("feature_promotion_allowlist.json"))

    def load_feature_promotion_denylist(self) -> dict[str, Any]:
        return self._read_first_json(self._feature_promotion_artifact_candidates("feature_promotion_denylist.json"))

    def load_feature_promotion_application_report(self) -> dict[str, Any]:
        return self._read_first_json(self._feature_promotion_artifact_candidates("feature_promotion_application_report.json"))

    def load_alpha_campaign_manifest(self) -> dict[str, Any]:
        return self._read_first_json(self._alpha_artifact_candidates("alpha_campaign_manifest.json"))

    def load_alpha_factory_report(self) -> dict[str, Any]:
        return self._read_first_json(self._alpha_artifact_candidates("alpha_factory_report.json"))

    def load_alpha_generation_stats(self) -> dict[str, Any]:
        return self._read_first_json(self._alpha_artifact_candidates("alpha_generation_stats.json"))

    def load_alpha_candidates(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._alpha_artifact_candidates("alpha_candidates.jsonl"))

    def load_alpha_static_checks(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._alpha_artifact_candidates("alpha_static_checks.jsonl"))

    def load_alpha_proxy_eval(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._alpha_artifact_candidates("alpha_proxy_eval.jsonl"))

    def load_alpha_proxy_eval_report(self) -> dict[str, Any]:
        return self._read_first_json(self._alpha_artifact_candidates("alpha_proxy_eval_report.json"))

    def load_alpha_full_eval_summary(self) -> dict[str, Any]:
        return self._read_first_json(self._alpha_artifact_candidates("alpha_full_eval_summary.json"))

    def load_alpha_scored_candidates(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._alpha_artifact_candidates("alpha_scored_candidates.jsonl"))

    def load_alpha_shortlist(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._alpha_artifact_candidates("alpha_shortlist.jsonl"))

    def load_alpha_rejected(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._alpha_artifact_candidates("alpha_rejected.jsonl"))

    def load_alpha_diversity_report(self) -> dict[str, Any]:
        return self._read_first_json(self._alpha_artifact_candidates("alpha_diversity_report.json"))

    def load_alpha_campaign_artifact_catalog(self) -> dict[str, Any]:
        return self._read_first_json(self._alpha_artifact_candidates("alpha_campaign_artifact_catalog.json"))

    def load_alpha_experiment_registry(self) -> dict[str, Any]:
        return self._read_first_json(self._alpha_store_artifact_candidates("alpha_experiment_registry.json"))

    def load_alpha_experiment_store_report(self) -> dict[str, Any]:
        return self._read_first_json(self._alpha_store_artifact_candidates("alpha_experiment_store_report.json"))

    def load_alpha_shards(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._alpha_store_artifact_candidates("alpha_shards.jsonl"))

    def load_alpha_consolidated_factors(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._alpha_store_artifact_candidates("alpha_consolidated_factors.jsonl"))

    def load_alpha_leaderboard(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._alpha_store_artifact_candidates("alpha_leaderboard.jsonl"))

    def load_alpha_factor_dedup_report(self) -> dict[str, Any]:
        return self._read_first_json(self._alpha_store_artifact_candidates("alpha_factor_dedup_report.json"))

    def load_alpha_campaign_comparison_report(self) -> dict[str, Any]:
        return self._read_first_json(self._alpha_store_artifact_candidates("alpha_campaign_comparison_report.json"))

    def load_alpha_validation_candidate_pool(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._alpha_store_artifact_candidates("alpha_validation_candidate_pool.jsonl"))

    def load_alpha_large_campaign_plan(self) -> dict[str, Any]:
        return self._read_first_json(self._alpha_store_artifact_candidates("alpha_large_campaign_plan.json") + self._experiment_artifact_candidates("alpha_large_campaign_plan.json"))

    def load_validation_campaign_registry(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("validation_campaign_registry.json"))

    def load_validation_campaign_store_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("validation_campaign_store_report.json"))

    def load_engineering_robustness_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("engineering_robustness_report.json"))

    def load_clean_holdout_campaign_plan(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("clean_holdout_campaign_plan.json"))

    def load_task_051_preflight_audit(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_051_preflight_audit.json"))

    def load_task_051_engineering_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_051_engineering_report.json"))

    def load_task_052_preflight_audit(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_052_preflight_audit.json"))

    def load_task_052_readiness(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_052_readiness.json"))

    def load_task_052_backfill_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_052_backfill_report.json"))

    def load_task_053_readiness(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_053_readiness.json"))

    def load_task_053_orchestrator_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_053_orchestrator_report.json"))

    def load_task_053_suspension_reconciliation(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_053_suspension_reconciliation.json"))

    def load_task_054_production_dag_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_054a_production_dag_report.json"))

    def load_task_054_firewall_sentinel(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_054a_production_firewall_sentinel.json"))

    def load_task_054b_production_dag(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_054b_production_dag_report.json"))

    def load_task_054b_evidence_package(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_054b_evidence_package.json"))

    def load_task_054c_production_sentinel(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task054c_production_sentinel.json"))

    def load_task_054c_pre_gpu_gate_seal(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task054c_pre_gpu_gate_seal.json"))

    def load_task_054c_final_verification(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task054c_final_verification.json"))

    def load_task_055a_observation_boundary_seal(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task055a_observation_boundary_seal.json"))

    def load_task_055a_simulation_bundle(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("simulation_bundle_manifest.json"))

    def load_task_055a_final_report(self) -> dict[str, Any]:
        candidates = self._validation_campaign_artifact_candidates("task055a_final_report.json")
        candidates.extend(self._validation_campaign_artifact_candidates("task055a_result.json"))
        return self._read_first_json(candidates)

    def load_task_055b_final_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task055b_final_report.json"))

    def load_task_055c_final_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task055c_final_report.json"))

    def load_task_055d_final_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task055d_final_report.json"))

    def load_task_055e_offline_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task055e_offline_report.json"))

    def load_task_055f_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task055f_report.json"))

    def load_task_055g_access_plan(self) -> dict[str, Any]:
        return self._read_first_json(self._task055g_artifact_candidates("access_plan.json"))

    def load_task_055g_access_ledger(self) -> dict[str, Any]:
        return self._read_first_json(self._task055g_artifact_candidates("access_ledger_manifest.json"))

    def load_task_055g_truth_v2(self) -> dict[str, Any]:
        return self._read_first_json(self._task055g_artifact_candidates("truth_v2_manifest.json"))

    def load_task_055g_fee_document_acquisition(self) -> dict[str, Any]:
        return self._read_first_json(self._task055g_artifact_candidates("fee_document_acquisition.json"))

    def load_task_055g_fee_schedule_v2(self) -> dict[str, Any]:
        return self._read_first_json(self._task055g_artifact_candidates("fee_schedule_v2_manifest.json"))

    def load_task_055g_operational_seal(self) -> dict[str, Any]:
        return self._read_first_json(self._task055g_artifact_candidates("operational_seal.json"))

    def load_task_055g_causal_frontier(self) -> dict[str, Any]:
        return self._read_first_json(self._task055g_artifact_candidates("causal_frontier_manifest.json"))

    def load_task_055g_network_plan(self) -> dict[str, Any]:
        candidates = self._task055g_artifact_candidates("round_one_exact_daily_plan.json")
        candidates.extend(self._task055g_artifact_candidates("round_one_network_plan.json"))
        return self._read_first_json(candidates)

    def load_task_055g_semantic_verification(self) -> dict[str, Any]:
        return self._read_first_json(self._task055g_artifact_candidates("semantic_verification.json"))

    def load_task_055g_final_report(self) -> dict[str, Any]:
        candidates = self._task055g_artifact_candidates("task055g_report.json")
        candidates.extend(self._task055g_artifact_candidates("task055g_final_report.json"))
        return self._read_first_json(candidates)

    def load_task_055g_final_verification(self) -> dict[str, Any]:
        return self._read_first_json(
            self._task055g_artifact_candidates("task055g_final_verification.json")
        )

    def load_task_054_scrubbed_evidence(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("task_054a_scrubbed_evidence_package.json"))

    def load_future_untouched_holdout_plan(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("future_untouched_holdout_plan.json"))

    def load_research_observation_ledger(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_campaign_artifact_candidates("research_observation_ledger.jsonl"))

    def load_validation_campaigns(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_campaign_artifact_candidates("validation_campaigns.jsonl"))

    def load_validation_candidates(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_campaign_artifact_candidates("validation_candidates.jsonl"))

    def load_validation_shards_campaign(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_campaign_artifact_candidates("validation_shards.jsonl"))

    def load_validation_candidate_results(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_campaign_artifact_candidates("validation_candidate_results.jsonl"))

    def load_validation_leaderboard(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_campaign_artifact_candidates("validation_leaderboard.jsonl"))

    def load_factor_certification_queue(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_campaign_artifact_candidates("factor_certification_queue.jsonl"))

    def load_factor_certification_campaign_registry(self) -> dict[str, Any]:
        return self._read_first_json(self._factor_certification_campaign_artifact_candidates("factor_certification_campaign_registry.json"))

    def load_factor_certification_campaign_report(self) -> dict[str, Any]:
        return self._read_first_json(self._factor_certification_campaign_artifact_candidates("factor_certification_campaign_report.json"))

    def load_factor_certification_campaigns(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._factor_certification_campaign_artifact_candidates("factor_certification_campaigns.jsonl"))

    def load_factor_certification_items(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._factor_certification_campaign_artifact_candidates("factor_certification_items.jsonl"))

    def load_certified_factor_pool(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._factor_certification_campaign_artifact_candidates("certified_factor_pool.jsonl"))

    def load_certified_factor_leaderboard(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._factor_certification_campaign_artifact_candidates("certified_factor_leaderboard.jsonl"))

    def load_portfolio_campaign_registry(self) -> dict[str, Any]:
        return self._read_first_json(self._portfolio_campaign_artifact_candidates("portfolio_certification_campaign_registry.json"))

    def load_portfolio_campaign_report(self) -> dict[str, Any]:
        return self._read_first_json(self._portfolio_campaign_artifact_candidates("portfolio_certification_campaign_report.json"))

    def load_portfolio_candidate_items(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._portfolio_campaign_artifact_candidates("portfolio_candidate_items.jsonl"))

    def load_production_candidate_bundle(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._portfolio_campaign_artifact_candidates("production_candidate_bundle.jsonl"))

    def load_production_candidate_bundle_report(self) -> dict[str, Any]:
        return self._read_first_json(self._portfolio_campaign_artifact_candidates("production_candidate_bundle_report.json"))

    def load_optimizer_policy_activation_queue(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._portfolio_campaign_artifact_candidates("optimizer_policy_activation_queue.jsonl"))

    def load_production_candidate_bundle_plan(self) -> dict[str, Any]:
        return self._read_first_json(
            self._portfolio_campaign_artifact_candidates("production_candidate_bundle_plan.json")
            + self._experiment_artifact_candidates("production_candidate_bundle_plan.json")
        )

    def load_validation_candidate_dedup_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("validation_candidate_dedup_report.json"))

    def load_validation_campaign_comparison_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_campaign_artifact_candidates("validation_campaign_comparison_report.json"))

    def load_validation_large_campaign_plan(self) -> dict[str, Any]:
        return self._read_first_json(
            self._validation_campaign_artifact_candidates("validation_large_campaign_plan.json")
            + self._experiment_artifact_candidates("validation_large_campaign_plan.json")
        )

    def load_validation_lab_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_artifact_candidates("validation_lab_report.json"))

    def load_validation_splits(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_artifact_candidates("validation_splits.jsonl"))

    def load_factor_validation_results(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_artifact_candidates("factor_validation_results.jsonl"))

    def load_factor_validation_summary(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_artifact_candidates("factor_validation_summary.json"))

    def load_multiple_testing_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_artifact_candidates("multiple_testing_report.json"))

    def load_overfit_risk_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_artifact_candidates("overfit_risk_report.json"))

    def load_placebo_test_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_artifact_candidates("placebo_test_report.json"))

    def load_placebo_trials(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_artifact_candidates("placebo_trials.jsonl"))

    def load_regime_validation_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_artifact_candidates("regime_validation_report.json"))

    def load_regime_results(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_artifact_candidates("regime_results.jsonl"))

    def load_sensitivity_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_artifact_candidates("sensitivity_report.json"))

    def load_sensitivity_results(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_artifact_candidates("sensitivity_results.jsonl"))

    def load_stress_backtest_report(self) -> dict[str, Any]:
        return self._read_first_json(self._validation_artifact_candidates("stress_backtest_report.json"))

    def load_stress_backtest_results(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_artifact_candidates("stress_backtest_results.jsonl"))

    def load_validation_issues(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._validation_artifact_candidates("validation_issues.jsonl"))

    def load_factor_certification_policy(self) -> dict[str, Any]:
        return self._read_first_json(self._certification_artifact_candidates("factor_certification_policy.json"))

    def load_factor_certification_scorecard(self) -> dict[str, Any]:
        return self._read_first_json(self._certification_artifact_candidates("factor_certification_scorecard.json"))

    def load_factor_certification_decision(self) -> dict[str, Any]:
        return self._read_first_json(self._certification_artifact_candidates("factor_certification_decision.json"))

    def load_factor_certification_package(self) -> dict[str, Any]:
        return self._read_first_json(self._certification_artifact_candidates("factor_certification_package.json"))

    def load_factor_certification_checks(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._certification_artifact_candidates("factor_certification_checks.jsonl"))

    def load_portfolio_lab_report(self) -> dict[str, Any]:
        return self._read_first_json(self._portfolio_lab_artifact_candidates("portfolio_lab_report.json"))

    def load_portfolio_robustness_report(self) -> dict[str, Any]:
        return self._read_first_json(self._portfolio_lab_artifact_candidates("portfolio_robustness_report.json"))

    def load_portfolio_policy_trials(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._portfolio_lab_artifact_candidates("portfolio_policy_trials.jsonl"))

    def load_portfolio_trial_metrics(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._portfolio_lab_artifact_candidates("portfolio_trial_metrics.jsonl"))

    def load_selected_portfolio_policy(self) -> dict[str, Any]:
        return self._read_first_json(self._portfolio_lab_artifact_candidates("selected_portfolio_policy.json"))

    def load_portfolio_certification_decision(self) -> dict[str, Any]:
        return self._read_first_json(self._portfolio_certification_artifact_candidates("portfolio_certification_decision.json"))

    def load_portfolio_certification_scorecard(self) -> dict[str, Any]:
        return self._read_first_json(self._portfolio_certification_artifact_candidates("portfolio_certification_scorecard.json"))

    def load_certified_portfolio_policy(self) -> dict[str, Any]:
        return self._read_first_json(self._portfolio_certification_artifact_candidates("certified_portfolio_policy.json"))

    def load_portfolio_policy_activation_request(self) -> dict[str, Any]:
        return self._read_first_json(self._portfolio_certification_artifact_candidates("portfolio_policy_activation_request.json"))

    def load_compute_resource_snapshot(self) -> dict[str, Any]:
        for path in self._compute_artifact_candidates("compute_resource_snapshot.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_compute_run_report(self) -> dict[str, Any]:
        for path in self._compute_artifact_candidates("compute_run_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_compute_jobs(self) -> pd.DataFrame:
        for path in self._compute_artifact_candidates("compute_jobs.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_compute_job_runs(self) -> pd.DataFrame:
        for path in self._compute_artifact_candidates("compute_job_runs.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_compute_scheduler_events(self) -> pd.DataFrame:
        for path in self._compute_artifact_candidates("compute_scheduler_events.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_compute_heartbeats(self) -> pd.DataFrame:
        for path in self._compute_artifact_candidates("compute_heartbeats.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_gpu_leases(self) -> pd.DataFrame:
        for path in self._compute_artifact_candidates("gpu_leases.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_experiment_plan(self) -> dict[str, Any]:
        return self._read_first_json(self._experiment_artifact_candidates("experiment_plan.json"))

    def load_experiment_graph(self) -> dict[str, Any]:
        return self._read_first_json(self._experiment_artifact_candidates("experiment_graph.json"))

    def load_experiment_run_report(self) -> dict[str, Any]:
        return self._read_first_json(self._experiment_artifact_candidates("experiment_run_report.json"))

    def load_experiment_resource_plan(self) -> dict[str, Any]:
        return self._read_first_json(self._experiment_artifact_candidates("experiment_resource_plan.json"))

    def load_experiment_shards(self) -> pd.DataFrame:
        for path in self._experiment_artifact_candidates("experiment_shards.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_experiment_merge_report(self) -> dict[str, Any]:
        return self._read_first_json(self._experiment_artifact_candidates("experiment_merge_report.json"))

    def load_experiment_artifact_catalog(self) -> dict[str, Any]:
        return self._read_first_json(self._experiment_artifact_candidates("experiment_artifact_catalog.json"))

    def load_distributed_training_report(self) -> dict[str, Any]:
        for path in self._neural_artifact_candidates("distributed_training_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_gpu_benchmark_report(self) -> dict[str, Any]:
        for path in self._benchmark_candidates("benchmark_result.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backtest_result(self) -> dict[str, Any]:
        return self._read_json(self.config.backtest_dir / "backtest_result.json")

    def load_equity_curve(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.backtest_dir / "equity_curve.jsonl")

    def load_trades(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.backtest_dir / "trades.jsonl")

    def load_target_positions(self) -> pd.DataFrame:
        return self._read_table("target_positions")

    def load_orders(self) -> pd.DataFrame:
        return self._read_table("orders")

    def load_paper_fills(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.orders_dir / "paper_fills.jsonl")

    def load_corporate_actions_report(self) -> dict[str, Any]:
        for path in self._corporate_action_candidates("corporate_actions_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_corporate_action_events(self) -> pd.DataFrame:
        for path in self._corporate_action_candidates("corporate_action_events.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_total_return_report(self) -> dict[str, Any]:
        for path in self._corporate_action_candidates("total_return_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_total_return_series(self) -> pd.DataFrame:
        for path in self._corporate_action_candidates("total_return_series.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_adjustment_reconciliation(self) -> dict[str, Any]:
        for path in self._corporate_action_candidates("adjustment_factor_reconciliation.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_capacity_report(self) -> dict[str, Any]:
        for path in self._execution_plan_candidates("capacity_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_execution_plan(self) -> dict[str, Any]:
        for path in self._execution_plan_candidates("execution_plan.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_parent_orders(self) -> pd.DataFrame:
        for path in self._execution_plan_candidates("parent_orders.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_child_orders(self) -> pd.DataFrame:
        for path in self._execution_plan_candidates("child_orders.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_child_fills(self) -> pd.DataFrame:
        for path in self._execution_plan_candidates("child_fills.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_execution_quality(self) -> dict[str, Any]:
        for path in self._execution_plan_candidates("execution_quality.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_report(self) -> dict[str, Any]:
        for path in self._broker_artifact_candidates("broker_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_reconciliation(self) -> dict[str, Any]:
        for path in self._broker_artifact_candidates("broker_reconciliation.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_orders(self) -> pd.DataFrame:
        for path in self._broker_artifact_candidates("broker_orders.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_broker_events(self) -> pd.DataFrame:
        for path in self._broker_artifact_candidates("broker_events.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_broker_fills(self) -> pd.DataFrame:
        for path in self._broker_artifact_candidates("broker_fills.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_broker_batch_summary(self) -> dict[str, Any]:
        for path in self._broker_artifact_candidates("broker_batch_summary.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_instruction_manifest(self) -> dict[str, Any]:
        for path in self._broker_artifact_candidates("broker_instruction_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_file_gateway_report(self) -> dict[str, Any]:
        return self._read_first_json(self._broker_file_gateway_candidates("broker_file_gateway_report.json"))

    def load_broker_file_manifest(self) -> dict[str, Any]:
        return self._read_first_json(self._broker_file_gateway_candidates("broker_file_manifest.json"))

    def load_broker_file_checksum_manifest(self) -> dict[str, Any]:
        return self._read_first_json(self._broker_file_gateway_candidates("broker_file_checksum_manifest.json"))

    def load_broker_file_roundtrip_report(self) -> dict[str, Any]:
        return self._read_first_json(self._broker_file_gateway_candidates("broker_file_roundtrip_report.json"))

    def load_operator_handoff_report(self) -> dict[str, Any]:
        return self._read_first_json(self._operator_handoff_candidates("operator_handoff_report.json"))

    def load_broker_mapping_certification_decision(self) -> dict[str, Any]:
        return self._read_first_json(self._mapping_certification_candidates("broker_mapping_certification_decision.json"))

    def load_broker_statement_manifest(self) -> dict[str, Any]:
        for path in self._statement_artifact_candidates("broker_statement_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_statement_import_report(self) -> dict[str, Any]:
        for path in self._statement_artifact_candidates("broker_statement_import_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_statement_parse_issues(self) -> pd.DataFrame:
        for path in self._statement_artifact_candidates("broker_statement_parse_issues.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_broker_statement_validation_report(self) -> dict[str, Any]:
        for path in self._statement_artifact_candidates("broker_statement_validation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_normalized_external(self, dataset: str) -> pd.DataFrame:
        filename = f"normalized_external_{dataset}.jsonl"
        for path in self._statement_artifact_candidates(filename):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_eod_reconciliation_report(self) -> dict[str, Any]:
        for path in self._reconciliation_artifact_candidates("eod_reconciliation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_reconciliation_breaks(self) -> pd.DataFrame:
        for path in self._reconciliation_artifact_candidates("reconciliation_breaks.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_external_account_mirror(self) -> dict[str, Any]:
        for path in self._reconciliation_artifact_candidates("external_account_mirror.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_external_mirror_table(self, name: str) -> pd.DataFrame:
        filename = f"external_{name}_mirror.jsonl"
        for path in self._reconciliation_artifact_candidates(filename):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_adjustment_proposals(self) -> pd.DataFrame:
        for path in self._reconciliation_artifact_candidates("adjustment_proposals.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_adjustment_proposal_batch(self) -> dict[str, Any]:
        for path in self._reconciliation_artifact_candidates("adjustment_proposal_batch.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_adjustment_application_result(self) -> dict[str, Any]:
        for path in self._reconciliation_artifact_candidates("adjustment_application_result.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_adjustment_ledger(self) -> pd.DataFrame:
        for path in self._account_candidates("adjustment_ledger.jsonl") + self._reconciliation_artifact_candidates("adjustment_ledger.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_production_run(self) -> dict[str, Any]:
        for path in self._production_candidates("production_run.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_production_run_markdown(self) -> str:
        for path in self._production_candidates("production_run.md"):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_production_orchestrator_report(self) -> dict[str, Any]:
        return self._read_first_json(self._production_orchestrator_candidates("production_orchestrator_report.json"))

    def load_production_run_plan(self) -> dict[str, Any]:
        return self._read_first_json(self._production_orchestrator_candidates("production_run_plan.json"))

    def load_production_readiness_report(self) -> dict[str, Any]:
        return self._read_first_json(self._production_orchestrator_candidates("production_readiness_report.json"))

    def load_production_phase_runs(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._production_orchestrator_candidates("production_phase_runs.jsonl"))

    def load_production_gate_results(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._production_orchestrator_candidates("production_gate_results.jsonl"))

    def load_production_run_events(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._production_orchestrator_candidates("production_run_events.jsonl"))

    def load_production_day_package(self) -> dict[str, Any]:
        return self._read_first_json(self._production_orchestrator_candidates("production_day_package.json"))

    def load_shadow_run_report(self) -> dict[str, Any]:
        return self._read_first_json(self._shadow_artifact_candidates("shadow_run_report.json"))

    def load_shadow_orders(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._shadow_artifact_candidates("shadow_orders.jsonl"))

    def load_shadow_fills(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._shadow_artifact_candidates("shadow_fills.jsonl"))

    def load_shadow_positions(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._shadow_artifact_candidates("shadow_positions.jsonl"))

    def load_shadow_account_snapshots(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._shadow_artifact_candidates("shadow_account_snapshots.jsonl"))

    def load_shadow_drift_report(self) -> dict[str, Any]:
        return self._read_first_json(self._shadow_artifact_candidates("shadow_drift_report.json"))

    def load_shadow_performance_report(self) -> dict[str, Any]:
        return self._read_first_json(self._shadow_artifact_candidates("shadow_performance_report.json"))

    def load_production_replay_report(self) -> dict[str, Any]:
        return self._read_first_json(self._production_replay_candidates("production_replay_report.json"))

    def load_production_replay_days(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._production_replay_candidates("production_replay_days.jsonl"))

    def load_production_replay_events(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._production_replay_candidates("production_replay_events.jsonl"))

    def load_shadow_lab_report(self) -> dict[str, Any]:
        return self._read_first_json(self._shadow_lab_candidates("shadow_lab_report.json"))

    def load_shadow_day_summaries(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._shadow_lab_candidates("shadow_day_summaries.jsonl"))

    def load_shadow_drift_summary(self) -> dict[str, Any]:
        return self._read_first_json(self._shadow_lab_candidates("shadow_drift_summary.json"))

    def load_shadow_calibration_suggestions(self) -> dict[str, Any]:
        return self._read_first_json(self._shadow_lab_candidates("shadow_calibration_suggestions.json"))

    def load_live_readiness_decision(self) -> dict[str, Any]:
        return self._read_first_json(self._live_readiness_candidates("live_readiness_decision.json"))

    def load_live_readiness_scorecard(self) -> dict[str, Any]:
        return self._read_first_json(self._live_readiness_candidates("live_readiness_scorecard.json"))

    def load_live_readiness_checks(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._live_readiness_candidates("live_readiness_checks.jsonl"))

    def load_program_trading_system_inventory(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("program_trading_system_inventory.json"))

    def load_program_trading_strategy_inventory(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("program_trading_strategy_inventory.json"))

    def load_program_trading_risk_control_inventory(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("program_trading_risk_control_inventory.json"))

    def load_program_trading_compliance_pack(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("program_trading_compliance_pack.json"))

    def load_program_trading_evidence_records(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._prelive_candidates("program_trading_evidence_records.jsonl"))

    def load_program_trading_compliance_checklist(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._prelive_candidates("program_trading_compliance_checklist.jsonl"))

    def load_compliance_gap_report(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("compliance_gap_report.json"))

    def load_secret_scan_report(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("secret_scan_report.json"))

    def load_secret_scan_findings(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._prelive_candidates("secret_scan_findings.jsonl"))

    def load_compliance_review_package(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("compliance_review_package.json"))

    def load_broker_uat_plan(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("broker_uat_plan.json"))

    def load_broker_uat_report(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("broker_uat_report.json"))

    def load_broker_uat_scenarios(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._prelive_candidates("broker_uat_scenarios.jsonl"))

    def load_broker_uat_results(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._prelive_candidates("broker_uat_results.jsonl"))

    def load_broker_adapter_capability_manifest(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("broker_adapter_capability_manifest.json"))

    def load_broker_adapter_contract_report(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("broker_adapter_contract_report.json"))

    def load_broker_uat_replay_report(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("broker_uat_replay_report.json"))

    def load_broker_uat_issues(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._prelive_candidates("broker_uat_issues.jsonl"))

    def load_broker_connectivity_report(self) -> dict[str, Any]:
        return self._read_first_json(self._broker_connectivity_candidates("broker_connectivity_report.json"))

    def load_broker_connectivity_profile(self) -> dict[str, Any]:
        return self._read_first_json(self._broker_connectivity_candidates("broker_connectivity_profile.json"))

    def load_broker_network_guard_report(self) -> dict[str, Any]:
        return self._read_first_json(self._broker_connectivity_candidates("broker_network_guard_report.json"))

    def load_broker_credential_ref_manifest(self) -> dict[str, Any]:
        return self._read_first_json(self._broker_connectivity_candidates("broker_credential_ref_manifest.json"))

    def load_broker_connectivity_sessions(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._broker_connectivity_candidates("broker_connectivity_sessions.jsonl"))

    def load_broker_connectivity_issues(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._broker_connectivity_candidates("broker_connectivity_issues.jsonl"))

    def load_broker_readonly_mirror_report(self) -> dict[str, Any]:
        return self._read_first_json(self._broker_readonly_mirror_candidates("broker_readonly_mirror_report.json"))

    def load_broker_readonly_snapshot(self) -> dict[str, Any]:
        return self._read_first_json(self._broker_readonly_mirror_candidates("broker_readonly_snapshot.json"))

    def load_readonly_mirror_reconciliation_report(self) -> dict[str, Any]:
        return self._read_first_json(self._broker_readonly_mirror_candidates("readonly_mirror_reconciliation_report.json"))

    def load_readonly_broker_positions(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._broker_readonly_mirror_candidates("readonly_broker_positions.jsonl"))

    def load_readonly_broker_orders(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._broker_readonly_mirror_candidates("readonly_broker_orders.jsonl"))

    def load_readonly_broker_fills(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._broker_readonly_mirror_candidates("readonly_broker_fills.jsonl"))

    def load_go_live_gate_policy(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("go_live_gate_policy.json"))

    def load_go_live_gate_scorecard(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("go_live_gate_scorecard.json"))

    def load_go_live_gate_decision(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("go_live_gate_decision.json"))

    def load_go_live_gate_checks(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._prelive_candidates("go_live_gate_checks.jsonl"))

    def load_go_live_review_package(self) -> dict[str, Any]:
        return self._read_first_json(self._prelive_candidates("go_live_review_package.json"))

    def load_incident_report(self) -> dict[str, Any]:
        return self._read_first_json(self._incident_artifact_candidates("incident_report.json"))

    def load_incident_records(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._incident_artifact_candidates("incident_records.jsonl"))

    def load_incident_events(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._incident_artifact_candidates("incident_events.jsonl"))

    def load_incident_runbook(self) -> dict[str, Any]:
        return self._read_first_json(self._incident_artifact_candidates("incident_runbook.json"))

    def load_approvals(self) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        for approvals_dir in self._approval_dir_candidates():
            if not approvals_dir.exists():
                continue
            for path in sorted((approvals_dir / "approvals").glob("*.json")):
                payload = self._read_json(path)
                if payload:
                    records.append(payload)
        return pd.DataFrame(records)

    def load_approval_log(self) -> pd.DataFrame:
        for approvals_dir in self._approval_dir_candidates():
            frame = self._read_jsonl(approvals_dir / "approval_log.jsonl")
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_paper_account_state(self) -> dict[str, Any]:
        for path in self._account_candidates("account_state.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_settlement_report(self) -> dict[str, Any]:
        for path in self._settlement_candidates("settlement_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_settlement_events(self) -> pd.DataFrame:
        for path in self._settlement_candidates("settlement_events.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_cash_buckets(self) -> pd.DataFrame:
        for path in self._settlement_candidates("cash_buckets.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_position_lots(self) -> pd.DataFrame:
        for path in self._settlement_candidates("position_lots.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_position_availability(self) -> pd.DataFrame:
        for path in self._settlement_candidates("position_availability.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_realized_pnl(self) -> pd.DataFrame:
        for path in self._settlement_candidates("realized_pnl.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_account_nav(self) -> pd.DataFrame:
        for path in self._settlement_candidates("account_nav.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_account_reconciliation_report(self) -> dict[str, Any]:
        for path in self._settlement_candidates("account_reconciliation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_account_performance_report(self) -> dict[str, Any]:
        for path in self._settlement_candidates("account_performance_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_fee_tax_report(self) -> dict[str, Any]:
        for path in self._settlement_candidates("fee_tax_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_paper_positions(self) -> pd.DataFrame:
        for path in self._account_candidates("positions.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_account_snapshots(self) -> pd.DataFrame:
        for path in self._account_candidates("account_snapshots.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_trade_ledger(self) -> pd.DataFrame:
        for path in self._account_candidates("trade_ledger.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_corporate_action_ledger(self) -> pd.DataFrame:
        for path in self._account_candidates("corporate_action_ledger.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_monitoring_report(self) -> dict[str, Any]:
        for path in self._monitoring_candidates("monitoring_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_monitoring_report_markdown(self) -> str:
        for path in self._monitoring_candidates("monitoring_report.md"):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_monitoring_alerts(self) -> pd.DataFrame:
        for path in self._monitoring_candidates("alerts.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_risk_control_report(self) -> dict[str, Any]:
        for path in self._risk_control_candidates("risk_control_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_risk_control_breaches(self) -> pd.DataFrame:
        for path in self._risk_control_candidates("risk_control_breaches.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_risk_limit_usage(self) -> pd.DataFrame:
        for path in self._risk_control_candidates("risk_limit_usage.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_kill_switch_state(self) -> dict[str, Any]:
        for path in self._risk_control_candidates("kill_switch_state.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_risk_override_records(self) -> pd.DataFrame:
        for path in self._risk_control_candidates("risk_override_records.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_matrix_metadata(self) -> dict[str, Any]:
        for path in self._matrix_candidates("metadata.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_matrix_validation_report(self) -> dict[str, Any]:
        for path in self._matrix_candidates("matrix_validation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_benchmark_result(self) -> dict[str, Any]:
        for path in self._benchmark_candidates("benchmark_result.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_benchmark_report_markdown(self) -> str:
        for path in self._benchmark_candidates("benchmark_report.md"):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_cross_source_report(self) -> dict[str, Any]:
        for path in self._cross_source_candidates("cross_source_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_cross_source_report_markdown(self) -> str:
        for path in self._cross_source_candidates("cross_source_report.md"):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_data_source_smoke_report(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("data_source_smoke_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_provider_probe(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("provider_probe.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_field_coverage_report(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("field_coverage.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_data_source_audit_summary(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("audit_summary.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_incremental_recovery_report(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("incremental_recovery_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_baseline_compare_summary(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("baseline_compare_summary.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_dataset_contracts(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("dataset_contracts.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backfill_plan(self) -> dict[str, Any]:
        for path in self._backfill_candidates("backfill_plan.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backfill_run_report(self) -> dict[str, Any]:
        for path in self._backfill_candidates("backfill_run_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backfill_coverage_report(self) -> dict[str, Any]:
        for path in self._backfill_candidates("backfill_coverage_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backfill_job_results(self) -> pd.DataFrame:
        for path in self._backfill_candidates("backfill_job_results.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_backfill_observer_report(self) -> dict[str, Any]:
        for path in self._backfill_candidates("backfill_observer_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backfill_dataset_progress(self) -> pd.DataFrame:
        for path in self._backfill_candidates("backfill_dataset_progress.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_backfill_eta_report(self) -> dict[str, Any]:
        for path in self._backfill_candidates("backfill_eta_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backfill_repair_plan(self) -> dict[str, Any]:
        for path in self._backfill_candidates("backfill_repair_plan.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backfill_repair_batch_plan(self) -> dict[str, Any]:
        return self._read_first_json(self._backfill_repair_candidates("repair_batch_plan.json"))

    def load_backfill_repair_run_report(self) -> dict[str, Any]:
        return self._read_first_json(self._backfill_repair_candidates("repair_run_report.json"))

    def load_backfill_repair_job_results(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._backfill_repair_candidates("repair_job_results.jsonl"))

    def load_backfill_repair_events(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._backfill_repair_candidates("repair_events.jsonl"))

    def load_backfill_postprocess_plan(self) -> dict[str, Any]:
        for path in self._backfill_candidates("backfill_postprocess_plan.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_raw_data_landing_report(self) -> dict[str, Any]:
        for path in self._raw_landing_candidates("raw_data_landing_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_raw_dataset_landing_checks(self) -> pd.DataFrame:
        for path in self._raw_landing_candidates("raw_dataset_landing_checks.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_raw_dataset_coverage_matrix(self) -> dict[str, Any]:
        for path in self._raw_landing_candidates("raw_dataset_coverage_matrix.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_raw_freeze_readiness_decision(self) -> dict[str, Any]:
        for path in self._raw_landing_candidates("raw_freeze_readiness_decision.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_raw_freeze_readiness_checks(self) -> pd.DataFrame:
        for path in self._raw_landing_candidates("raw_freeze_readiness_checks.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_raw_data_index_manifest(self) -> dict[str, Any]:
        return self._read_first_json(self._raw_data_index_candidates("raw_data_index_manifest.json"))

    def load_raw_dataset_indexes(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._raw_data_index_candidates("raw_dataset_indexes.jsonl"))

    def load_raw_partitions(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._raw_data_index_candidates("raw_partitions.jsonl"))

    def load_raw_data_index_report(self) -> dict[str, Any]:
        return self._read_first_json(self._raw_data_index_candidates("raw_data_index_report.json"))

    def load_raw_data_index_validation_report(self) -> dict[str, Any]:
        return self._read_first_json(self._raw_data_index_candidates("raw_data_index_validation_report.json"))

    def load_raw_data_index_issues(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._raw_data_index_candidates("raw_data_index_issues.jsonl"))

    def load_data_quality_lab_report(self) -> dict[str, Any]:
        return self._read_first_json(self._data_quality_candidates("data_quality_lab_report.json"))

    def load_data_quality_scorecard(self) -> dict[str, Any]:
        return self._read_first_json(self._data_quality_candidates("data_quality_scorecard.json"))

    def load_data_quality_rules(self) -> dict[str, Any]:
        return self._read_first_json(self._data_quality_candidates("data_quality_rules.json"))

    def load_data_quality_issues(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._data_quality_candidates("data_quality_issues.jsonl"))

    def load_dataset_quality_summary(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._data_quality_candidates("dataset_quality_summary.jsonl"))

    def load_cross_dataset_quality_report(self) -> dict[str, Any]:
        return self._read_first_json(self._data_quality_candidates("cross_dataset_quality_report.json"))

    def load_data_quality_repair_suggestions(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._data_quality_candidates("data_quality_repair_suggestions.jsonl"))

    def load_data_quality_freeze_gate(self) -> dict[str, Any]:
        return self._read_first_json(self._data_quality_candidates("data_quality_freeze_gate.json"))

    def load_research_data_readiness_report(self) -> dict[str, Any]:
        return self._read_first_json(self._research_readiness_candidates("research_data_readiness_report.json"))

    def load_research_dataset_readiness(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._research_readiness_candidates("research_dataset_readiness.jsonl"))

    def load_feature_readiness_catalog(self) -> dict[str, Any]:
        return self._read_first_json(self._research_readiness_candidates("feature_readiness_catalog.json"))

    def load_research_readiness_decision(self) -> dict[str, Any]:
        return self._read_first_json(self._research_readiness_candidates("research_readiness_decision.json"))

    def load_research_readiness_remediations(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._research_readiness_candidates("research_readiness_remediations.jsonl"))

    def load_post_download_plan(self) -> dict[str, Any]:
        return self._read_first_json(self._post_download_candidates("post_download_plan.json"))

    def load_post_download_steps(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._post_download_candidates("post_download_steps.jsonl"))

    def load_post_download_run_report(self) -> dict[str, Any]:
        return self._read_first_json(self._post_download_candidates("post_download_run_report.json"))

    def load_post_download_step_runs(self) -> pd.DataFrame:
        return self._read_first_jsonl(self._post_download_candidates("post_download_step_runs.jsonl"))

    def load_post_download_state(self) -> dict[str, Any]:
        return self._read_first_json(self._post_download_candidates("post_download_state.json"))

    def load_post_download_final_package(self) -> dict[str, Any]:
        return self._read_first_json(self._post_download_candidates("post_download_final_package.json"))

    def load_post_download_artifact_catalog(self) -> dict[str, Any]:
        return self._read_first_json(self._post_download_candidates("post_download_artifact_catalog.json"))

    def load_freeze_candidate_package(self) -> dict[str, Any]:
        return self._read_first_json(self._post_download_candidates("freeze_candidate_package.json"))

    def load_data_lake_report(self) -> dict[str, Any]:
        for path in self._data_lake_candidates("data_lake_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_dataset_version_manifest(self) -> dict[str, Any]:
        for path in self._data_lake_candidates("dataset_version_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_research_data_freeze(self) -> dict[str, Any]:
        for path in self._data_lake_candidates("research_data_freeze.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_freeze_validation_report(self) -> dict[str, Any]:
        for path in self._data_lake_candidates("freeze_validation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_data_lineage_graph(self) -> dict[str, Any]:
        for path in self._data_lake_candidates("data_lineage_graph.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_artifact_validation_report(self) -> dict[str, Any]:
        for path in self._schema_artifact_candidates("artifact_validation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_artifact_schema_manifest(self) -> dict[str, Any]:
        for path in self._schema_artifact_candidates("artifact_schema_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_release_gate_report(self) -> dict[str, Any]:
        for path in self._release_artifact_candidates("release_gate_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_release_manifest(self) -> dict[str, Any]:
        for path in self._release_artifact_candidates("release_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_dependency_inventory(self) -> dict[str, Any]:
        for path in self._release_artifact_candidates("dependency_inventory.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_module_inventory(self) -> dict[str, Any]:
        for path in self._release_artifact_candidates("module_inventory.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_cli_inventory(self) -> dict[str, Any]:
        for path in self._release_artifact_candidates("cli_inventory.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_ci_report(self) -> dict[str, Any]:
        for path in self._ci_artifact_candidates("ci_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_formula_corpus_stats(self) -> dict[str, Any]:
        for path in self._formula_corpus_candidates("formula_corpus_stats.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_formula_corpus(self) -> pd.DataFrame:
        for path in self._formula_corpus_candidates("formula_corpus.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_formula_batch_eval_result(self) -> dict[str, Any]:
        for path in self._formula_batch_eval_candidates("formula_batch_eval_result.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_formula_eval_results(self) -> pd.DataFrame:
        for path in self._formula_batch_eval_candidates("formula_eval_results.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_alphagpt_pretrain_result(self) -> dict[str, Any]:
        for path in self._pretrain_artifact_candidates("alphagpt_pretrain_result.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_alphagpt_pretrain_history(self) -> pd.DataFrame:
        for path in self._pretrain_artifact_candidates("alphagpt_pretrain_history.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_model_registry_report(self) -> dict[str, Any]:
        for path in self._model_registry_candidates("model_registry_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_model_registry_manifest(self) -> dict[str, Any]:
        for path in self._model_registry_candidates("model_registry_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_model_versions(self) -> pd.DataFrame:
        for path in self._model_registry_candidates("model_versions.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_model_deployments(self) -> pd.DataFrame:
        for path in self._model_registry_candidates("model_deployments.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_model_lifecycle_events(self) -> pd.DataFrame:
        for path in self._model_registry_candidates("lifecycle_events.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_model_lineage_graph(self) -> dict[str, Any]:
        for path in self._model_registry_candidates("model_lineage_graph.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_factor_lifecycle_report(self) -> dict[str, Any]:
        for path in self._model_lifecycle_candidates("factor_lifecycle_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_factor_health_checks(self) -> pd.DataFrame:
        for path in self._model_lifecycle_candidates("factor_health_checks.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_lifecycle_decisions(self) -> pd.DataFrame:
        for path in self._model_lifecycle_candidates("lifecycle_decisions.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_model_review_package(self) -> dict[str, Any]:
        for path in self._model_lifecycle_candidates("model_review_package.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_pit_validation_report(self) -> dict[str, Any]:
        for path in self._pit_artifact_candidates("pit_validation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_pit_dataset_manifest(self) -> dict[str, Any]:
        for path in self._pit_artifact_candidates("pit_dataset_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_pit_dataset_contracts(self) -> dict[str, Any]:
        for path in self._pit_artifact_candidates("pit_dataset_contracts.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_security_lifecycle(self) -> pd.DataFrame:
        for path in self._pit_artifact_candidates("security_lifecycle.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_active_security_mask(self) -> pd.DataFrame:
        for path in self._pit_artifact_candidates("active_security_mask.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_survivorship_bias_report(self) -> dict[str, Any]:
        for path in self._pit_artifact_candidates("survivorship_bias_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_universe_pit_summary(self) -> dict[str, Any]:
        for path in self._pit_artifact_candidates("universe_pit_summary.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_leakage_audit_report(self) -> dict[str, Any]:
        for path in self._leakage_artifact_candidates("leakage_audit_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_leakage_issues(self) -> pd.DataFrame:
        for path in self._leakage_artifact_candidates("leakage_issues.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_formula_leakage_scan(self) -> dict[str, Any]:
        for path in self._leakage_artifact_candidates("formula_leakage_scan.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_truncation_consistency_report(self) -> dict[str, Any]:
        for path in self._leakage_artifact_candidates("truncation_consistency_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_factor_value_leakage_report(self) -> dict[str, Any]:
        for path in self._leakage_artifact_candidates("factor_value_leakage_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backtest_leakage_report(self) -> dict[str, Any]:
        for path in self._leakage_artifact_candidates("backtest_leakage_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def _neural_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.report_dir / filename,
            root / "neural" / filename,
            root / "search" / filename,
            root / "search" / "neural" / filename,
            root / "suite" / "search" / filename,
            root / "suite" / "search" / "neural" / filename,
            root / "suite" / "alphagpt_pretrain" / filename,
            self.config.pretrain_dir / filename,
        ]

    def _neural_checkpoint_candidates(self) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.report_dir / "checkpoints",
            root / "neural" / "checkpoints",
            root / "search" / "checkpoints",
            root / "search" / "neural" / "checkpoints",
            root / "suite" / "search" / "checkpoints",
            root / "suite" / "search" / "neural" / "checkpoints",
            root / "suite" / "alphagpt_pretrain" / "checkpoints",
            self.config.pretrain_dir / "checkpoints",
        ]

    def _formula_corpus_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.formula_corpus_dir / filename,
            root / "formula_corpus" / filename,
            root / "suite" / "formula_corpus" / filename,
        ]

    def _formula_batch_eval_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.formula_batch_eval_dir / filename,
            root / "formula_batch_eval" / filename,
            root / "suite" / "formula_batch_eval" / filename,
        ]

    def _pretrain_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.pretrain_dir / filename,
            root / "alphagpt_pretrain" / filename,
            root / "suite" / "alphagpt_pretrain" / filename,
        ]

    def _model_registry_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.model_registry_dir / filename,
            root / "model_registry" / filename,
            root / "suite" / "model_registry" / filename,
            root / "production" / filename,
            root / "production_execute" / filename,
        ]

    def _model_lifecycle_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.model_lifecycle_dir / filename,
            root / "model_lifecycle" / filename,
            root / "suite" / "model_lifecycle" / filename,
        ]

    def _pit_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.pit_dir / filename,
            root / "pit" / filename,
            root / "pit_mask" / filename,
            root / "suite_pit" / filename,
            root / "suite" / "point_in_time" / filename,
            root / "data" / "universe" / filename,
        ]

    def _leakage_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.leakage_dir / filename,
            root / "leakage" / filename,
            root / "suite_leakage" / filename,
            root / "backtest_leakage" / filename,
            root / "suite" / "leakage_audit" / filename,
            self.config.backtest_dir / "leakage_audit" / filename,
        ]

    def _production_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.production_dir / filename,
            root / "production" / filename,
            root / "production_execute" / filename,
        ]

    def _production_orchestrator_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.production_orchestrator_dir / filename,
            self.config.production_dir / filename,
            root / "production_orchestrator" / filename,
            root / "production_plan" / filename,
            root / "production_propose" / filename,
            root / "production_shadow" / filename,
            root / "production_execute" / filename,
            root / "production_execute_replay" / filename,
            root / "production_close" / filename,
        ]

    def _shadow_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.shadow_trading_dir / filename,
            self.config.production_orchestrator_dir / "shadow" / filename,
            root / "shadow" / filename,
            root / "shadow_trading" / filename,
            root / "production_shadow" / "shadow" / filename,
            root / "production_propose" / "shadow" / filename,
        ]

    def _production_replay_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.production_replay_dir / filename,
            root / "production_replay" / filename,
            root / "replay" / filename,
        ]

    def _shadow_lab_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.shadow_lab_dir / filename,
            root / "shadow_lab" / filename,
        ]

    def _live_readiness_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.live_readiness_dir / filename,
            root / "live_readiness" / filename,
            root / "readiness" / filename,
        ]

    def _incident_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.incident_dir / filename,
            self.config.production_orchestrator_dir / "incidents" / filename,
            root / "incidents" / filename,
            root / "production_orchestrator" / "incidents" / filename,
            root / "production_propose" / "incidents" / filename,
            root / "production_execute" / "incidents" / filename,
            root / "production_close" / "incidents" / filename,
        ]

    def _approval_dir_candidates(self) -> list[Path]:
        root = self.config.report_dir.parent
        return [self.config.approval_store_dir, root / "approvals"]

    def _account_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [self.config.paper_account_dir / filename, root / "account" / filename]

    def _settlement_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.paper_account_dir / filename,
            self.config.paper_account_dir / "settlement" / filename,
            self.config.backtest_dir / "settlement" / filename,
            self.config.backtest_dir / filename,
            self.config.orders_dir / "settlement" / filename,
            self.config.orders_dir / filename,
            root / "settlement" / filename,
            root / "settlement" / "backtest" / filename,
            root / "settlement" / "orders" / filename,
            root / "production" / "settlement" / filename,
            root / "production_execute" / "settlement" / filename,
            root / "production_execute_replay" / "settlement" / filename,
            root / "account" / filename,
        ]

    def _monitoring_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [self.config.monitoring_dir / filename, root / "monitoring" / filename]

    def _matrix_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.matrix_cache_dir / filename,
            self.config.data_dir / "matrix_cache" / filename,
            root / "data" / "matrix_cache" / filename,
        ]

    def _benchmark_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.benchmark_dir / filename,
            root / "benchmark" / filename,
            root / "suite_benchmark" / filename,
            root / "suite" / "benchmark" / filename,
        ]

    def _cross_source_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.cross_source_dir / filename,
            root / "cross_source" / filename,
        ]

    def _data_source_smoke_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.data_source_smoke_dir / filename,
            root / "data_source_smoke" / filename,
            root / "sample_smoke" / filename,
            root / "fake_tushare_smoke" / filename,
            root / "real_tushare_smoke" / filename,
        ]

    def _backfill_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.backfill_dir / filename,
            self.config.backfill_dir / "observer" / filename,
            self.config.backfill_dir / "landing" / filename,
            root / "backfill" / filename,
            root / "backfill_observer" / filename,
            root / "raw_landing" / filename,
            root / "landing" / filename,
            root / "data_backfill" / filename,
            root / "sample_backfill" / filename,
            root / "fake_tushare_backfill" / filename,
            self.config.data_dir / filename,
        ]

    def _raw_landing_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.backfill_dir / "landing" / filename,
            self.config.backfill_dir / filename,
            root / "raw_landing" / filename,
            root / "landing" / filename,
            root / "raw_data_landing" / filename,
            self.config.data_dir / filename,
        ]

    def _raw_data_index_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.raw_data_index_dir / filename,
            self.config.data_dir / "raw_data_index" / filename,
            self.config.real_data_dir / "raw_index_latest" / filename,
            self.config.real_data_dir / "raw_data_index" / filename,
            root / "raw_data_index" / filename,
            root / "raw_index_latest" / filename,
            root / "post_download" / "raw_index_latest" / filename,
            root / "real_data" / "raw_index_latest" / filename,
            root / "real_data_sample" / "raw_index_latest" / filename,
            root / "real_data_fake_tushare" / "raw_index_latest" / filename,
            root / filename,
        ]

    def _data_quality_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.data_quality_lab_dir / filename,
            self.config.real_data_dir / "data_quality_latest" / filename,
            self.config.backfill_dir / "data_quality_latest" / filename,
            root / "data_quality_lab" / filename,
            root / "data_quality" / filename,
            root / "data_quality_latest" / filename,
            root / "post_download" / "data_quality_latest" / filename,
            root / "real_data" / "data_quality_latest" / filename,
            root / "real_data_sample" / "data_quality_latest" / filename,
            root / "real_data_fake_tushare" / "data_quality_latest" / filename,
            root / filename,
        ]

    def _backfill_repair_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.backfill_dir / "repair" / filename,
            self.config.backfill_dir / filename,
            root / "backfill_repair" / filename,
            root / "repair" / filename,
            root / "post_download" / "repair" / filename,
        ]

    def _research_readiness_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.backfill_dir / "research_readiness" / filename,
            self.config.backfill_dir / filename,
            root / "research_readiness" / filename,
            root / "research_data_readiness" / filename,
            root / "readiness" / filename,
            root / "data_backfill" / "readiness" / filename,
            self.config.data_dir / filename,
        ]

    def _post_download_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.backfill_dir / "post_download" / filename,
            self.config.backfill_dir / filename,
            root / "post_download" / filename,
            root / "post_download_orchestrator" / filename,
            root / "data_backfill" / "post_download" / filename,
        ]

    def _data_lake_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.data_lake_dir / filename,
            root / "data_lake" / filename,
            root / "suite" / "data_version" / filename,
            root / "suite" / "freeze_validation" / filename,
            root / "sample_smoke" / "data_lake" / filename,
            root / "sample_smoke" / "research_freeze" / filename,
            root / "research_freeze" / filename,
        ]

    def _schema_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.schema_validation_dir / filename,
            root / "schema_validation" / filename,
            root / "schema" / filename,
            self.config.ci_dir / "schema" / filename,
            self.config.release_dir / filename,
        ]

    def _release_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.release_dir / filename,
            root / "release" / filename,
            self.config.ci_dir / "release" / filename,
        ]

    def _ci_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.ci_dir / filename,
            root / "local_ci" / filename,
            root / "ci" / filename,
        ]

    def _risk_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.backtest_dir / filename,
            self.config.backtest_dir / "risk" / filename,
            self.config.orders_dir / filename,
            root / "backtest" / filename,
            root / "backtest_direct" / filename,
        ]

    def _risk_control_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.orders_dir / "risk_controls" / filename,
            self.config.orders_dir / filename,
            self.config.backtest_dir / "risk_controls" / filename,
            root / "risk_controls" / filename,
            root / "production" / "risk_controls" / filename,
            root / "production_execute" / "risk_controls" / filename,
            root / "production_execute_replay" / "risk_controls" / filename,
            root / "backtest" / "risk_controls" / filename,
            root / "backtest_direct" / "risk_controls" / filename,
        ]

    def _execution_plan_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.orders_dir / "plan" / filename,
            self.config.orders_dir / filename,
            self.config.backtest_dir / "execution_plan" / filename,
            root / "execution_plan" / filename,
            root / "orders_capacity" / "plan" / filename,
            root / "daily_orders" / "plan" / filename,
            root / "daily_orders_execute" / "plan" / filename,
        ]

    def _corporate_action_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        candidates = [
            self.config.report_dir / filename,
            self.config.report_dir / "corporate_actions" / filename,
            self.config.backtest_dir / "corporate_actions" / filename,
            self.config.orders_dir / "corporate_actions" / filename,
            root / "corporate_actions" / filename,
            root / "suite" / "corporate_actions" / filename,
            root / "production" / "corporate_actions" / filename,
            root / "production_execute" / "corporate_actions" / filename,
        ]
        if filename == "corporate_action_events.jsonl":
            candidates.append(self.config.data_dir / "corporate_actions" / "records.jsonl")
        else:
            candidates.append(self.config.data_dir / filename)
        return candidates

    def _broker_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.orders_dir / "broker" / filename,
            self.config.orders_dir / filename,
            self.config.orders_dir / "outbox" / filename,
            root / "production_execute" / "broker" / filename,
            root / "production_execute_replay" / "broker" / filename,
            root / "broker" / filename,
            root / "broker_file" / "outbox" / filename,
        ]

    def _broker_file_gateway_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.broker_file_gateway_dir / filename,
            self.config.broker_file_gateway_dir / "outbox" / filename,
            self.config.orders_dir / "broker_file_gateway" / filename,
            self.config.orders_dir / "plan" / filename,
            root / "broker_file_gateway" / filename,
            root / "production_execute" / "broker_file_gateway" / filename,
            root / "production_execute" / filename,
            root / "production_execute_replay" / "broker_file_gateway" / filename,
            root / "daily_orders_execute" / "broker_file_gateway" / filename,
        ]

    def _operator_handoff_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.operator_handoff_dir / filename,
            root / "operator_handoff" / filename,
            root / "production_execute" / "operator_handoff" / filename,
            root / "production_execute" / filename,
            root / "production_execute_replay" / "operator_handoff" / filename,
        ]

    def _mapping_certification_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.broker_mapping_certification_dir / filename,
            root / "broker_mapping_certification" / filename,
            root / "mapping_certification" / filename,
            root / "mapping" / filename,
        ]

    def _prelive_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        production_root = self.config.production_dir
        return [
            self.config.program_trading_compliance_dir / filename,
            self.config.broker_uat_dir / filename,
            self.config.go_live_gate_dir / filename,
            production_root / "compliance" / filename,
            production_root / "program_trading_compliance" / filename,
            production_root / "broker_uat" / filename,
            production_root / "broker_uat_lab" / filename,
            production_root / "go_live_gate" / filename,
            production_root / "prelive_gate" / filename,
            production_root / filename,
            root / "compliance" / filename,
            root / "program_trading_compliance" / filename,
            root / "broker_uat" / filename,
            root / "broker_uat_lab" / filename,
            root / "go_live_gate" / filename,
            root / "prelive_gate" / filename,
            root / "production_execute" / filename,
            root / filename,
        ]

    def _broker_connectivity_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.broker_connectivity_dir / filename,
            self.config.broker_uat_dir / "broker_connectivity" / filename,
            self.config.production_dir / "broker_connectivity" / filename,
            root / "broker_connectivity" / filename,
            root / "broker_uat" / "broker_connectivity" / filename,
            root / "broker_uat_lab" / "broker_connectivity" / filename,
            root / "production_execute" / "broker_connectivity" / filename,
            root / filename,
        ]

    def _broker_readonly_mirror_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.broker_readonly_mirror_dir / filename,
            self.config.broker_uat_dir / "broker_readonly_mirror" / filename,
            self.config.production_dir / "broker_readonly_mirror" / filename,
            root / "broker_readonly_mirror" / filename,
            root / "broker_uat" / "broker_readonly_mirror" / filename,
            root / "broker_uat_lab" / "broker_readonly_mirror" / filename,
            root / "production_execute" / "broker_readonly_mirror" / filename,
            root / filename,
        ]

    def _compute_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.compute_dir / filename,
            self.config.compute_dir / "compute_state" / filename,
            root / "compute_state" / filename,
            root / "compute_probe" / filename,
            root / "suite" / "compute_state" / filename,
            root / "compute_suite" / filename,
        ]

    def _experiment_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.experiment_dir / filename,
            self.config.experiment_dir / "merged" / filename,
            root / "experiment" / filename,
            root / "experiment" / "merged" / filename,
            root / "experiment_suite" / filename,
            root / "suite" / "experiment" / filename,
        ]

    def _feature_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.feature_factory_dir / filename,
            root / "features" / filename,
            root / "features_v2" / filename,
            root / "features_v3" / filename,
            root / "suite_features_v2" / filename,
            root / "suite_features_v3" / filename,
            root / "alpha_factory" / "features" / filename,
            root / "suite_alpha_factory" / "features" / filename,
        ]

    def _feature_promotion_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            root / "feature_promotion" / filename,
            root / "features_v3" / filename,
            root / "suite_feature_promotion" / filename,
            root / "suite_features_v3" / filename,
            self.config.feature_factory_dir / "feature_promotion" / filename,
            self.config.feature_factory_dir / filename,
        ]

    def _alpha_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.alpha_factory_dir / filename,
            root / "alpha_factory" / filename,
            root / "suite_alpha_factory" / filename,
            root / "suite" / "alpha_factory" / filename,
        ]

    def _alpha_store_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.alpha_experiment_store_dir / filename,
            self.config.alpha_factory_dir / "alpha_experiment_store" / filename,
            root / "alpha_experiment_store" / filename,
            root / "alpha_factory" / "alpha_experiment_store" / filename,
            root / "suite" / "alpha_experiment_store" / filename,
        ]

    def _validation_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.validation_lab_dir / filename,
            self.config.backtest_dir / "validation" / filename,
            root / "validation_lab" / filename,
            root / "suite" / "validation_lab" / filename,
            root / "validation" / filename,
        ]

    def _validation_campaign_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.validation_campaign_store_dir / filename,
            root / "validation_campaign_store" / filename,
            root / "validation_campaign" / filename,
            root / "suite" / "validation_campaign_store" / filename,
            root / "experiment" / filename,
        ]

    def _task055g_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        candidates = [
            self.config.validation_campaign_store_dir / "task_055_g" / filename,
            self.config.validation_campaign_store_dir / "task055g" / filename,
            root / "task_055_g" / filename,
            root / "task055g" / filename,
        ]
        for search_root in (self.config.validation_campaign_store_dir, root):
            if not search_root.is_dir():
                continue
            for pattern in (f"task_055_g*/**/{filename}", f"validation_runs/task_055_g*/**/{filename}"):
                candidates.extend(sorted(search_root.glob(pattern), reverse=True))
        candidates.extend(self._validation_campaign_artifact_candidates(filename))
        return list(dict.fromkeys(candidates))

    def _certification_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.factor_certification_dir / filename,
            root / "factor_certification" / filename,
            root / "suite" / "factor_certification" / filename,
        ]

    def _factor_certification_campaign_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.factor_certification_campaign_dir / filename,
            root / "factor_certification_campaign" / filename,
            root / "certification_campaign_store" / filename,
            root / "suite" / "factor_certification_campaign" / filename,
        ]

    def _portfolio_lab_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.portfolio_lab_dir / filename,
            root / "portfolio_lab" / filename,
            root / "suite" / "portfolio_lab" / filename,
        ]

    def _portfolio_certification_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.portfolio_certification_dir / filename,
            root / "portfolio_certification" / filename,
            root / "suite" / "portfolio_certification" / filename,
        ]

    def _portfolio_campaign_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.portfolio_campaign_dir / filename,
            root / "portfolio_campaign" / filename,
            root / "portfolio_campaign_store" / filename,
            root / "suite" / "portfolio_campaign" / filename,
            root / "experiment" / filename,
        ]

    def _real_data_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.data_dir / filename,
            self.config.real_data_dir / filename,
            self.config.data_lake_dir / filename,
            self.config.backfill_dir / filename,
            self.config.data_source_smoke_dir / filename,
            root / "real_data" / filename,
            root / "real_data_sample" / filename,
            root / "real_data_fake_tushare" / filename,
            root / "data_smoke" / "sample_smoke" / filename,
            root / "sample_smoke" / filename,
            root / "production_data" / filename,
            root / filename,
        ]

    def _matrix_refresh_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.matrix_refresh_dir / filename,
            self.config.real_data_dir / "matrix_refresh" / filename,
            self.config.data_dir / "matrix_refresh" / filename,
            self.config.data_dir / "matrix_cache" / filename,
            root / "matrix_refresh" / filename,
            root / "real_data" / "matrix_refresh" / filename,
            root / "real_data_sample" / "matrix_refresh" / filename,
            root / "real_data_fake_tushare" / "matrix_refresh" / filename,
            root / filename,
        ]

    def _read_first_json(self, paths: list[Path]) -> dict[str, Any]:
        for path in paths:
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def _read_first_jsonl(self, paths: list[Path]) -> pd.DataFrame:
        for path in paths:
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def _statement_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            root / "statement_import_zero" / filename,
            root / "statement_import_mismatch" / filename,
            root / "statement_import" / filename,
            root / "external_statement_zero" / filename,
            root / "external_statement_mismatch" / filename,
            root / "production_reconcile_only" / "eod_reconciliation" / "statement_import" / filename,
            root / "production_execute" / "eod_reconciliation" / "statement_import" / filename,
        ]

    def _reconciliation_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            root / "eod_reconciliation_zero" / filename,
            root / "eod_reconciliation_mismatch" / filename,
            root / "eod_adjustment_approval" / filename,
            root / "eod_adjustment_apply" / filename,
            root / "operations_eod_reconciliation" / filename,
            root / "production_reconcile_only" / filename,
            root / "production_reconcile_only" / "eod_reconciliation" / filename,
            root / "production_execute" / "eod_reconciliation" / filename,
            root / "monitoring" / filename,
        ]

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _read_jsonl(path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame()
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return pd.DataFrame(records)

    def _read_table(self, stem: str) -> pd.DataFrame:
        jsonl_path = self.config.orders_dir / f"{stem}.jsonl"
        csv_path = self.config.orders_dir / f"{stem}.csv"
        frame = self._read_jsonl(jsonl_path)
        if not frame.empty:
            return frame
        if csv_path.exists():
            return pd.read_csv(csv_path)
        return pd.DataFrame()


DashboardService = AshareDashboardService
