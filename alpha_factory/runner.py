"""Alpha Factory campaign runner."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact
from compute_cluster import ComputeDeviceType, ComputeJobKind, ComputeJobSpec, ComputeSchedulerConfig, LocalComputeScheduler
from data_lake import validate_research_input
from factor_store import FactorRecord, LocalFactorStore, make_factor_id
from formula_batch_eval import FormulaBatchEvalConfig, FormulaBatchEvaluator, FormulaEvalRequest, merge_shard_outputs
from model_core.data_loader import AShareDataLoader

from feature_factory import (
    build_feature_semantics_map,
    build_feature_set_manifest,
    build_feature_tensor_artifacts,
    load_feature_manifest,
    make_formula_vocab_from_manifest,
)
from feature_promotion import load_promotion_gate

from .diversity import select_shortlist, write_diversity_outputs
from .generators import generate_alpha_candidates
from .full_research import run_full_research
from .models import AlphaCampaignConfig, AlphaCampaignManifest, AlphaFactoryReport
from .novelty import score_novelty
from .proxy_eval import run_proxy_eval
from .research_policy import load_alpha_research_policy
from research_firewall.lineage import build_loader_lineage
from .report import write_artifact_catalog, write_campaign_report, write_generation_stats, write_jsonl
from .scoring import score_candidates
from .static_checks import run_static_checks
from .trial_ledger import write_trial_ledger


class AlphaFactoryRunner:
    def __init__(self, config: AlphaCampaignConfig):
        _validate_production_research_config(config)
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.store = LocalFactorStore(config.factor_store_dir)
        self.paths: dict[str, str] = {}
        self.warnings: list[str] = []
        self.current_feature_manifest = None
        self.research_policy = load_alpha_research_policy(
            config.research_policy_id,
            production_research=config.production_research,
        )

    def run(self) -> AlphaFactoryReport:
        created_at = _utc_now()
        readiness = _alpha_factory_readiness(self.config.research_readiness_decision_path)
        if self.config.require_alpha_factory_ready and not readiness["ready"]:
            return self._blocked_report(created_at, readiness)
        freeze = validate_research_input(
            data_dir=self.config.data_dir,
            data_freeze_dir=self.config.data_freeze_dir,
            require_freeze=self.config.require_data_freeze,
        )
        if freeze.error_count:
            raise RuntimeError(f"data freeze validation failed: {freeze.status}")
        data_dir = _resolve_data_dir(
            self.config.data_dir,
            self.config.data_freeze_dir,
            self.config.canonical_research_view_manifest_path,
            production_research=self.config.production_research,
        )
        manifest = self._feature_manifest(freeze)
        self.current_feature_manifest = manifest
        promotion_gate = load_promotion_gate(
            policy_path=self.config.feature_promotion_policy_path,
            allowlist_path=self.config.feature_promotion_allowlist_path,
            denylist_path=self.config.feature_promotion_denylist_path,
            require_promotion=self.config.require_feature_promotion,
            allow_risk_filter_features=self.config.allow_risk_filter_features,
        )
        campaign = self._campaign_manifest(created_at, freeze, manifest)
        self.paths["alpha_research_policy_path"] = str(
            write_json_artifact(
                self.output_dir / "alpha_research_policy.json",
                {
                    "policy_id": self.research_policy.policy_id,
                    "policy_hash": self.research_policy.policy_hash,
                    "policy": self.research_policy.to_dict(),
                    "parameters_locked": self.research_policy.parameters_locked,
                    "certification_supported": False,
                },
                "alpha_research_policy",
                "alpha_factory",
            )
        )
        self.paths["alpha_campaign_manifest_path"] = str(
            write_json_artifact(
                self.output_dir / "alpha_campaign_manifest.json",
                campaign.to_dict(),
                "alpha_campaign_manifest",
                "alpha_factory",
            )
        )
        candidates = self._load_or_generate_candidates(campaign, manifest)
        candidates, static_rows = run_static_checks(
            candidates,
            max_complexity=self.config.max_complexity,
            max_lookback=self.config.max_lookback,
            vocab=make_formula_vocab_from_manifest(manifest),
            promotion_gate=promotion_gate,
            feature_meta=_feature_meta(manifest),
            feature_semantics=build_feature_semantics_map(manifest),
        )
        self.paths["alpha_static_checks_path"] = str(
            write_jsonl_artifact(self.output_dir / "alpha_static_checks.jsonl", static_rows, "alpha_static_checks", "alpha_factory")
        )
        loader = AShareDataLoader(
            data_dir=data_dir,
            device=None if self.config.device == "auto" else self.config.device,
            universe_name=self.config.universe_name,
            universe_file=self.config.universe_file,
            matrix_cache_dir=self.config.matrix_cache_dir,
            use_matrix_cache=bool(
                self.config.matrix_cache_dir
                and any((Path(self.config.matrix_cache_dir) / name).exists() for name in ("task_052a_strict_matrix_manifest.json", "metadata.json"))
            ),
            point_in_time=self.config.point_in_time,
            feature_cutoff_mode=self.config.feature_cutoff_mode,
            corporate_action_aware=self.config.corporate_action_aware,
            target_return_mode=self.config.target_return_mode,
            feature_set_name=manifest.feature_set_name,
            feature_set_manifest_path=self.paths.get("feature_set_manifest_path") or self.config.feature_set_manifest_path,
            research_end_date=self.config.research_end_date,
            holdout_start_date=self.config.holdout_start_date,
            label_horizon=self.config.label_horizon,
            canonical_feature_tensor_path=self.config.canonical_feature_tensor_path,
            canonical_feature_validity_tensor_path=self.config.canonical_feature_validity_tensor_path,
            production_research=self.config.production_research,
        ).load_data()
        novelty = score_novelty(candidates, self.store.load_factors())
        reference_matrices, reference_root = self._proxy_reference_matrices(loader)
        proxy_context_hash = _proxy_context_hash(candidates, novelty, reference_root, self.research_policy.policy_hash)
        candidates, proxy_rows, proxy_summary = self._load_or_run_proxy(
            candidates,
            loader,
            novelty,
            reference_matrices,
            proxy_context_hash,
        )
        full_rows, full_summary = self._run_full_eval(candidates, loader, data_dir, campaign)
        candidates, scored_rows = score_candidates(candidates, proxy_rows, full_rows, novelty)
        if self.config.use_batch_eval:
            evaluated_hashes = {
                str((row.get("request") or {}).get("formula_hash"))
                for row in full_rows
                if isinstance(row, dict)
                and isinstance(row.get("request"), dict)
                and row.get("status") not in {"error", "invalid"}
            }
            candidates = [
                item
                if item.formula_hash in evaluated_hashes or item.status == "rejected"
                else replace(item, status="rejected", reject_reason="not_selected_for_full_eval")
                for item in candidates
            ]
            rejected_ids = {
                item.alpha_candidate_id
                for item in candidates
                if item.reject_reason == "not_selected_for_full_eval"
            }
            scored_rows = [
                row
                if row.get("alpha_candidate_id") not in rejected_ids
                else row | {"status": "rejected", "reject_reason": "not_selected_for_full_eval"}
                for row in scored_rows
            ]
        self.paths["alpha_scored_candidates_path"] = str(
            write_jsonl_artifact(
                self.output_dir / "alpha_scored_candidates.jsonl",
                scored_rows,
                "alpha_scored_candidates",
                "alpha_factory",
            )
        )
        shortlist, rejected, diversity_report = select_shortlist(
            candidates,
            top_k=self.config.top_k,
            max_per_family=max(self.config.max_per_family, 1),
            min_novelty_score=self.config.min_novelty_score,
        )
        self.paths.update(write_diversity_outputs(shortlist, rejected, diversity_report, self.output_dir))
        trial_paths, selection_bias_summary = write_trial_ledger(
            candidates=candidates,
            static_rows=static_rows,
            proxy_rows=proxy_rows,
            full_rows=full_rows,
            scored_rows=scored_rows,
            shortlist=shortlist,
            campaign_id=campaign.campaign_id,
            policy_id=self.research_policy.policy_id,
            policy_hash=self.research_policy.policy_hash,
            output_dir=self.output_dir,
        )
        self.paths.update(trial_paths)
        full_summary["selection_bias"] = selection_bias_summary
        if self.config.register_shortlist:
            full_summary["registered_shortlist_factors"] = self._register_shortlist_metadata(
                shortlist,
                full_rows,
                campaign.campaign_id,
            )
        summary = self._summary(
            candidates,
            static_rows,
            proxy_summary,
            full_summary,
            shortlist,
            diversity_report,
            manifest,
            campaign.campaign_id,
        )
        report = AlphaFactoryReport(
            campaign_id=campaign.campaign_id,
            status="success",
            summary=summary,
            paths=self.paths,
            warnings=self.warnings,
        )
        report_json, report_md = write_campaign_report(report, self.output_dir)
        self.paths["alpha_factory_report_path"] = str(report_json)
        self.paths["alpha_factory_report_md_path"] = str(report_md)
        catalog_path = write_artifact_catalog(self.paths, self.output_dir, campaign.campaign_id)
        self.paths["alpha_campaign_artifact_catalog_path"] = str(catalog_path)
        self._register_experiment_store(campaign, report_json)
        report = AlphaFactoryReport(campaign.campaign_id, "success", summary, self.paths, self.warnings)
        write_campaign_report(report, self.output_dir)
        return report

    def _blocked_report(self, created_at: str, readiness: dict[str, Any]) -> AlphaFactoryReport:
        campaign_id = _campaign_id(self.config.campaign_name, created_at, self.config.seed)
        summary = {
            "alpha_factory_enabled": True,
            "alpha_campaign_id": campaign_id,
            "blocked_reason": "research readiness does not allow alpha factory",
            "research_readiness": readiness,
            "total_trials": 0,
            "evaluated_trials": 0,
            "selected_trials": 0,
        }
        self.paths["research_readiness_decision_path"] = str(self.config.research_readiness_decision_path or "")
        report = AlphaFactoryReport(
            campaign_id=campaign_id,
            status="blocked",
            summary=summary,
            paths=self.paths,
            warnings=["alpha factory blocked by research readiness gate"],
        )
        report_json, report_md = write_campaign_report(report, self.output_dir)
        self.paths["alpha_factory_report_path"] = str(report_json)
        self.paths["alpha_factory_report_md_path"] = str(report_md)
        report = AlphaFactoryReport(campaign_id, "blocked", summary, self.paths, report.warnings)
        write_campaign_report(report, self.output_dir)
        return report

    def _register_experiment_store(self, campaign, report_json: Path) -> None:
        if not self.config.alpha_experiment_store_dir:
            return
        if not (
            self.config.register_experiment
            or self.config.consolidate_shards
            or self.config.write_leaderboard
            or self.config.validation_candidate_pool_dir
        ):
            return
        from alpha_experiment_store import ingest_alpha_factory_run
        from alpha_experiment_store.comparison import compare_experiment_stores

        shard_dirs = [str(path) for path in self._shard_factor_store_dirs()]
        if not shard_dirs and Path(self.config.factor_store_dir, "factors.jsonl").exists():
            shard_dirs = [self.config.factor_store_dir]
        result = ingest_alpha_factory_run(
            self.config.alpha_experiment_store_dir,
            campaign_report_path=report_json,
            campaign_manifest_path=self.paths.get("alpha_campaign_manifest_path"),
            paths=self.paths | {"factor_store_dir": self.config.factor_store_dir},
            shard_factor_store_dirs=shard_dirs,
            experiment_id=self.config.experiment_id or campaign.campaign_id,
            consolidate_shards=self.config.consolidate_shards,
            consolidated_factor_store_dir=self.config.consolidated_factor_store_dir,
            write_leaderboard_flag=self.config.write_leaderboard,
            validation_candidate_pool_dir=self.config.validation_candidate_pool_dir,
            leaderboard_top_k=self.config.leaderboard_top_k,
            max_validation_candidates=self.config.max_validation_candidates,
            previous_experiment_dirs=self.config.previous_experiment_dirs,
        )
        self.paths.update(result.get("paths", {}))
        if self.config.dedupe_across_campaigns and self.config.previous_experiment_dirs:
            comparison = compare_experiment_stores(
                self.config.alpha_experiment_store_dir,
                self.config.previous_experiment_dirs,
                self.config.alpha_experiment_store_dir,
            )
            self.paths["alpha_campaign_comparison_report_path"] = str(
                Path(self.config.alpha_experiment_store_dir) / "alpha_campaign_comparison_report.json"
            )
            if comparison.get("overlap_count", 0):
                self.warnings.append(f"cross-campaign duplicate formulas: {comparison.get('overlap_count')}")

    def _shard_factor_store_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        eval_dir = Path(self.config.batch_eval_dir) if self.config.batch_eval_dir else self.output_dir / "batch_eval"
        for path in sorted((eval_dir / "shards").glob("shard_*/factor_store")):
            if (path / "factors.jsonl").exists():
                dirs.append(path)
        return dirs

    def _feature_manifest(self, freeze) -> Any:
        if self.config.feature_set_manifest_path:
            manifest = load_feature_manifest(self.config.feature_set_manifest_path)
            self.paths["feature_set_manifest_path"] = self.config.feature_set_manifest_path
            return manifest
        manifest = build_feature_set_manifest(
            self.config.feature_set_name,
            data_freeze_id=freeze.freeze_id,
            data_freeze_hash=freeze.content_hash,
            point_in_time=self.config.point_in_time,
            corporate_action_aware=self.config.corporate_action_aware,
            target_return_mode=self.config.target_return_mode,
        )
        if self.config.build_feature_set or self.config.feature_set_name != "ashare_features_v1":
            feature_dir = Path(self.config.feature_output_dir) if self.config.feature_output_dir else self.output_dir / "features"
            data_dir = _resolve_data_dir(
                self.config.data_dir,
                self.config.data_freeze_dir,
                self.config.canonical_research_view_manifest_path,
                production_research=self.config.production_research,
            )
            loader = AShareDataLoader(
                data_dir=data_dir,
                device=None if self.config.device == "auto" else self.config.device,
                matrix_cache_dir=self.config.matrix_cache_dir,
                use_matrix_cache=bool(
                    self.config.matrix_cache_dir
                    and any((Path(self.config.matrix_cache_dir) / name).exists() for name in ("task_052a_strict_matrix_manifest.json", "metadata.json"))
                ),
                point_in_time=self.config.point_in_time,
                corporate_action_aware=self.config.corporate_action_aware,
                target_return_mode=self.config.target_return_mode,
                research_end_date=self.config.research_end_date,
                holdout_start_date=self.config.holdout_start_date,
                label_horizon=self.config.label_horizon,
                canonical_feature_tensor_path=self.config.canonical_feature_tensor_path,
                canonical_feature_validity_tensor_path=self.config.canonical_feature_validity_tensor_path,
                production_research=self.config.production_research,
            ).load_data()
            result = build_feature_tensor_artifacts(
                loader,
                feature_dir,
                feature_set_name=self.config.feature_set_name,
                data_freeze_id=freeze.freeze_id,
                data_freeze_hash=freeze.content_hash,
                point_in_time=self.config.point_in_time,
                corporate_action_aware=self.config.corporate_action_aware,
                target_return_mode=self.config.target_return_mode,
            )
            self.paths["feature_set_manifest_path"] = result.manifest_path
            self.paths["feature_coverage_report_path"] = result.coverage_report_path
            self.paths["feature_values_summary_path"] = result.values_summary_path
        return manifest

    def _campaign_manifest(self, created_at: str, freeze, feature_manifest) -> AlphaCampaignManifest:
        campaign_id = _campaign_id(self.config.campaign_name, created_at, self.config.seed)
        return AlphaCampaignManifest(
            campaign_id=campaign_id,
            campaign_name=self.config.campaign_name,
            data_freeze_id=freeze.freeze_id,
            data_freeze_hash=freeze.content_hash,
            feature_set_name=feature_manifest.feature_set_name,
            feature_set_version=feature_manifest.feature_set_version,
            feature_version=feature_manifest.feature_version,
            operator_version=feature_manifest.operator_version,
            formula_corpus_hash=_file_hash(self.config.formula_corpus_path),
            generator_budgets={
                "candidate_budget": self.config.candidate_budget,
                "template_budget": self.config.template_budget,
                "random_budget": self.config.random_budget,
                "mutation_budget": self.config.mutation_budget,
                "crossover_budget": self.config.crossover_budget,
                "corpus_budget": self.config.corpus_budget,
                "neural_budget": self.config.neural_budget,
            },
            random_seed=self.config.seed,
            compute_config={
                "use_compute_scheduler": self.config.use_compute_scheduler,
                "shard_count": self.config.shard_count,
                "max_parallel_gpu_jobs": self.config.max_parallel_gpu_jobs,
                "max_parallel_cpu_jobs": self.config.max_parallel_cpu_jobs,
            },
            config_snapshot=self.config.to_dict(),
            created_at=created_at,
        )

    def _load_or_generate_candidates(self, campaign, manifest):
        candidates_path = self.output_dir / "alpha_candidates.jsonl"
        if candidates_path.exists() and not self.config.refresh_candidates:
            candidates = [_candidate_from_dict(json.loads(line)) for line in candidates_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        else:
            candidates, warnings = generate_alpha_candidates(self.config, manifest)
            self.warnings.extend(warnings)
            write_jsonl_artifact(candidates_path, [item.to_dict() for item in candidates], "alpha_candidates", "alpha_factory")
            write_generation_stats(candidates, warnings, self.output_dir)
        self.paths["alpha_candidates_path"] = str(candidates_path)
        self.paths["alpha_generation_stats_path"] = str(self.output_dir / "alpha_generation_stats.json")
        return candidates

    def _load_or_run_proxy(self, candidates, loader, novelty, reference_matrices, proxy_context_hash):
        proxy_path = self.output_dir / "alpha_proxy_eval.jsonl"
        report_path = self.output_dir / "alpha_proxy_eval_report.json"
        expected_lineage = build_loader_lineage(
            loader,
            stage="alpha_proxy_eval",
            extra={
                "max_dates": int(max(self.config.proxy_max_dates, 1)),
                "seed": int(self.config.seed),
                "research_policy_hash": self.research_policy.policy_hash,
                "existing_factor_count": len(reference_matrices),
                "proxy_context_hash": proxy_context_hash,
            },
        )
        if proxy_path.exists() and report_path.exists() and not self.config.refresh_proxy:
            rows = [json.loads(line) for line in proxy_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            summary = json.loads(report_path.read_text(encoding="utf-8")).get("summary", {})
            if summary.get("lineage_hash") != expected_lineage["lineage_hash"]:
                rows = []
                summary = {}
            else:
                by_candidate = {str(row.get("alpha_candidate_id") or ""): row for row in rows}
                restored = []
                for candidate in candidates:
                    row = by_candidate.get(candidate.alpha_candidate_id)
                    if row is None:
                        restored.append(candidate)
                        continue
                    status = str(row.get("status") or candidate.status)
                    restored.append(
                        replace(
                            candidate,
                            proxy_score=float(row.get("proxy_score", candidate.proxy_score) or 0.0),
                            status=status,
                            reject_reason=None if status == "proxy_passed" else candidate.reject_reason or "proxy_cache_rejected",
                        )
                    )
                return restored, rows, summary
        candidates, rows, summary = run_proxy_eval(
            candidates,
            loader,
            max_candidates=max(self.config.proxy_max_candidates, 0),
            max_dates=max(self.config.proxy_max_dates, 1),
            vocab=make_formula_vocab_from_manifest(self.current_feature_manifest) if self.current_feature_manifest is not None else None,
            seed=self.config.seed,
            policy=self.research_policy,
            family_novelty_scores=novelty,
            existing_factor_matrices=reference_matrices,
            proxy_context_hash=proxy_context_hash,
        )
        self.paths["alpha_proxy_eval_path"] = str(write_jsonl_artifact(proxy_path, rows, "alpha_proxy_eval", "alpha_factory"))
        self.paths["alpha_proxy_eval_report_path"] = str(
            write_json_artifact(report_path, {"summary": summary, "rows": len(rows)}, "alpha_proxy_eval_report", "alpha_factory")
        )
        return candidates, rows, summary

    def _proxy_reference_matrices(self, loader) -> tuple[list[Any], str]:
        records = self.store.load_factors()
        if not records:
            return [], hashlib.sha256(b"empty_reference_factor_set").hexdigest()
        if not self.config.production_research:
            from factor_engine.correlation import load_existing_factor_matrices

            matrices = list(
                load_existing_factor_matrices(
                    self.store,
                    [record.factor_id for record in records],
                    ts_codes=loader.ts_codes,
                    trade_dates=loader.trade_dates,
                    device=loader.feat_tensor.device,
                ).values()
            )
            digest = hashlib.sha256()
            for matrix in matrices:
                digest.update(matrix.detach().float().cpu().contiguous().numpy().tobytes())
            return matrices, digest.hexdigest()
        from validation_lab.materialization import load_materialized_factor

        matrices = []
        manifest_hashes = []
        missing = []
        for record in records:
            metadata = record.metadata or {}
            manifest_path = metadata.get("materialization_manifest_path")
            if not manifest_path and isinstance(metadata.get("materialization_manifest"), dict):
                manifest_path = metadata["materialization_manifest"].get("manifest_path")
            if not manifest_path:
                missing.append(record.factor_id)
                continue
            values, _, _ = load_materialized_factor(manifest_path)
            if tuple(values.shape) != tuple(loader.target_ret.shape):
                raise RuntimeError(f"reference factor axis mismatch: {record.factor_id}")
            matrices.append(values.to(device=loader.feat_tensor.device))
            manifest_hashes.append(_file_hash(str(manifest_path)))
        if missing:
            raise RuntimeError("production reference factors require compact materialization: " + ",".join(sorted(missing)))
        return matrices, hashlib.sha256("\n".join(sorted(str(value) for value in manifest_hashes)).encode("utf-8")).hexdigest()

    def _run_full_eval(self, candidates, loader, data_dir: str, campaign) -> tuple[list[dict], dict[str, Any]]:
        eligible = [item for item in candidates if item.status == "proxy_passed"]
        selected = sorted(eligible, key=lambda item: (-float(item.proxy_score), item.alpha_candidate_id))
        if self.config.full_eval_max_candidates > 0:
            selected = selected[: self.config.full_eval_max_candidates]
        selection_summary = {
            "proxy_passed_candidates": len(eligible),
            "selected_for_full_eval": len(selected),
            "full_eval_max_candidates": self.config.full_eval_max_candidates,
        }
        self.paths["alpha_proxy_shortlist_path"] = str(
            write_jsonl_artifact(
                self.output_dir / "alpha_proxy_shortlist.jsonl",
                [
                    item.to_dict()
                    | {
                        "proxy_rank": rank,
                        "selection_stage": "cheap_proxy_shortlist",
                        "research_policy_id": self.research_policy.policy_id,
                        "research_policy_hash": self.research_policy.policy_hash,
                    }
                    for rank, item in enumerate(selected, start=1)
                ],
                "alpha_proxy_shortlist",
                "alpha_factory",
            )
        )
        if not self.config.use_batch_eval:
            summary = {
                "enabled": False,
                "evaluated": 0,
                "research_policy_id": self.research_policy.policy_id,
                "research_policy_hash": self.research_policy.policy_hash,
                "score_method": "dimensionless_cohort_multi_objective_v1",
                "normalization": {"method": "empirical_cdf_average_ties_v1", "candidate_count": 0, "reference_hash": hashlib.sha256(b"full_research_disabled").hexdigest()},
                "multiple_testing": {"method": "benjamini_hochberg_and_holm_v1", "total_generated_trials": len(candidates), "full_research_trials": 0},
                "selection_bias": {"total_trials": len(candidates), "full_research_trials": 0, "selection_fraction": 0.0, "selection_data_reused": True, "untouched_holdout": False},
                "certification_ready": False,
                **selection_summary,
            }
            self.paths["alpha_full_eval_summary_path"] = str(
                write_json_artifact(self.output_dir / "alpha_full_eval_summary.json", summary, "alpha_full_eval_summary", "alpha_factory")
            )
            return [], summary
        eval_dir = Path(self.config.batch_eval_dir) if self.config.batch_eval_dir else self.output_dir / "batch_eval"
        if not selected:
            eval_dir.mkdir(parents=True, exist_ok=True)
            empty_summary = {
                "total": 0,
                "evaluated_trial_count": 0,
                "unique_formula_hash_count": 0,
                "status_counts": {},
                "validation_candidates": 0,
                "research_rejected": 0,
                "errors": 0,
                "cache_hits": 0,
                "top": [],
                "enabled": True,
                "evaluated": 0,
                "research_policy_id": self.research_policy.policy_id,
                "research_policy_hash": self.research_policy.policy_hash,
                "score_method": "dimensionless_cohort_multi_objective_v1",
                "normalization": {"method": "empirical_cdf_average_ties_v1", "candidate_count": 0, "reference_hash": hashlib.sha256(b"empty_full_research").hexdigest()},
                "multiple_testing": {"method": "benjamini_hochberg_and_holm_v1", "total_generated_trials": len(candidates), "full_research_trials": 0},
                "selection_bias": {"total_trials": len(candidates), "full_research_trials": 0, "selection_fraction": 0.0, "selection_data_reused": True, "untouched_holdout": False},
                "certification_ready": False,
                **selection_summary,
            }
            result_path = write_json_artifact(
                eval_dir / "formula_batch_eval_result.json",
                {
                    "batch_id": campaign.campaign_id,
                    "status": "success",
                    "results": [],
                    "summary": empty_summary,
                    "paths": {},
                    "cache_manifest": {"enabled": bool(self.config.use_eval_cache), "cache_hits": 0, "cache_writes": 0},
                    "benchmark": {"formulas_requested": 0, "formulas_evaluated": 0, "formulas_per_second": 0.0},
                },
                "formula_batch_eval_result",
                "alpha_factory",
            )
            results_path = write_jsonl_artifact(
                eval_dir / "formula_eval_results.jsonl",
                [],
                "formula_eval_results",
                "alpha_factory",
            )
            self.paths["formula_batch_eval_result_path"] = str(result_path)
            self.paths["formula_eval_results_path"] = str(results_path)
            self.paths["alpha_full_eval_summary_path"] = str(
                write_json_artifact(self.output_dir / "alpha_full_eval_summary.json", empty_summary, "alpha_full_eval_summary", "alpha_factory")
            )
            return [], empty_summary
        requests = [
            FormulaEvalRequest(
                name=item.alpha_candidate_id,
                formula_tokens=item.formula_tokens,
                formula_names=item.formula_names,
                formula_hash=item.formula_hash,
                source=item.source,
                complexity=item.complexity,
                lookback=item.lookback,
                metadata={
                    "alpha_campaign_id": campaign.campaign_id,
                    "alpha_candidate_id": item.alpha_candidate_id,
                    "alpha_family_tags": item.family_tags,
                    "feature_set_name": item.feature_set_name,
                    "feature_version": item.feature_version,
                    "proxy_score": item.proxy_score,
                },
            )
            for item in selected
        ]
        if self._should_run_full_eval_with_scheduler():
            execution_rows, execution_summary = self._run_full_eval_with_scheduler(requests, data_dir, campaign, eval_dir)
            return self._run_governed_full_research(
                selected,
                loader,
                execution_rows,
                execution_summary | selection_summary,
                len(candidates),
            )
        result = FormulaBatchEvaluator(
            FormulaBatchEvalConfig(
                data_dir=data_dir,
                universe_name=self.config.universe_name,
                universe_file=self.config.universe_file,
                factor_store_dir=self.config.factor_store_dir,
                report_dir=self.config.report_dir or str(self.output_dir / "reports"),
                output_dir=str(eval_dir),
                matrix_cache_dir=self.config.matrix_cache_dir,
                use_matrix_cache=bool(
                    self.config.matrix_cache_dir
                    and any((Path(self.config.matrix_cache_dir) / name).exists() for name in ("task_052a_strict_matrix_manifest.json", "metadata.json"))
                ),
                device=self.config.batch_eval_device,
                strict_device=self.config.production_research,
                factor_transform=self.config.factor_transform,
                enable_gate=self.config.enable_gate,
                correlation_threshold=self.config.correlation_threshold,
                min_coverage=self.config.min_coverage,
                chunk_size=self.config.batch_eval_chunk_size,
                use_eval_cache=self.config.use_eval_cache,
                eval_cache_dir=self.config.eval_cache_dir,
                register_approved=False,
                batch_id=campaign.campaign_id,
                continue_on_error=True,
                shard_count=max(self.config.shard_count, 1),
                feature_set_name=self.config.feature_set_name,
                feature_set_manifest_path=self.config.feature_set_manifest_path or self.paths.get("feature_set_manifest_path"),
                alpha_campaign_id=campaign.campaign_id,
                feature_promotion_policy_hash=_promotion_policy_hash(
                    self.config.feature_promotion_allowlist_path,
                    self.config.feature_promotion_policy_path,
                ),
                research_end_date=self.config.research_end_date,
                holdout_start_date=self.config.holdout_start_date,
                label_horizon=self.config.label_horizon,
                canonical_feature_tensor_path=self.config.canonical_feature_tensor_path,
                canonical_feature_validity_tensor_path=self.config.canonical_feature_validity_tensor_path,
                production_research=self.config.production_research,
            )
        ).run(requests)
        execution_rows = [item.to_dict() for item in result.results]
        execution_summary = result.summary | {
            "enabled": True,
            "evaluated": len(execution_rows),
            "batch_id": result.batch_id,
            "formula_batch_eval_result_path": result.paths.get("formula_batch_eval_result_path"),
            **selection_summary,
        }
        self.paths["formula_batch_eval_result_path"] = result.paths.get("formula_batch_eval_result_path", "")
        self.paths["formula_eval_results_path"] = result.paths.get("formula_eval_results_path", "")
        return self._run_governed_full_research(
            selected,
            loader,
            execution_rows,
            execution_summary,
            len(candidates),
        )

    def _run_governed_full_research(self, selected, loader, execution_rows, execution_summary, total_trial_count):
        execution_by_hash = {
            str((row.get("request") or {}).get("formula_hash") or ""): row
            for row in execution_rows
            if isinstance(row, dict) and isinstance(row.get("request"), dict)
        }
        executable = [
            candidate
            for candidate in selected
            if str((execution_by_hash.get(candidate.formula_hash) or {}).get("status") or "")
            not in {"", "invalid", "error"}
        ]
        rows, research_summary = run_full_research(
            executable,
            loader,
            policy=self.research_policy,
            vocab=make_formula_vocab_from_manifest(self.current_feature_manifest),
            factor_transform=self.config.factor_transform,
            total_trial_count=int(total_trial_count),
            seed=self.config.seed,
        )
        by_hash = {str(row.get("formula_hash") or ""): row for row in rows}
        for candidate in selected:
            execution = execution_by_hash.get(candidate.formula_hash)
            if candidate.formula_hash not in by_hash:
                rows.append(
                    {
                        "alpha_candidate_id": candidate.alpha_candidate_id,
                        "factor_id": make_factor_id(candidate.formula_hash),
                        "formula_hash": candidate.formula_hash,
                        "request": {
                            "name": candidate.alpha_candidate_id,
                            "formula_hash": candidate.formula_hash,
                            "formula_tokens": candidate.formula_tokens,
                            "formula_names": candidate.formula_names,
                            "lookback": candidate.lookback,
                            "complexity": candidate.complexity,
                        },
                        "status": "data_blocked",
                        "score": 0.0,
                        "data_blockers": ["formula_execution_not_completed"],
                        "gate_reasons": ["formula_execution_not_completed"],
                        "certification_supported": False,
                    }
                )
                by_hash[candidate.formula_hash] = rows[-1]
            by_hash[candidate.formula_hash]["formula_execution"] = {
                "status": (execution or {}).get("status"),
                "cache_hit": bool((execution or {}).get("cache_hit", False)),
                "elapsed_seconds": float((execution or {}).get("elapsed_seconds", 0.0) or 0.0),
                "device": execution_summary.get("device") or execution_summary.get("scheduler_status"),
            }
        summary = {
            **execution_summary,
            **research_summary,
            "formula_execution_count": len(execution_rows),
            "evaluated": len(rows),
            "governed_full_research": True,
        }
        status_counts: dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        summary["status_counts"] = status_counts
        summary["semantic_content_hash"] = hashlib.sha256(
            json.dumps(
                [
                    {key: value for key, value in row.items() if key != "formula_execution"}
                    | {"formula_execution_status": (row.get("formula_execution") or {}).get("status")}
                    for row in rows
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.paths["alpha_full_eval_results_path"] = str(
            write_jsonl_artifact(
                self.output_dir / "alpha_full_eval_results.jsonl",
                rows,
                "alpha_full_eval_results",
                "alpha_factory",
            )
        )
        self.paths["alpha_full_eval_summary_path"] = str(
            write_json_artifact(
                self.output_dir / "alpha_full_eval_summary.json",
                summary,
                "alpha_full_eval_summary",
                "alpha_factory",
            )
        )
        return rows, summary

    def _should_run_full_eval_with_scheduler(self) -> bool:
        return bool(
            self.config.use_batch_eval
            and self.config.use_compute_scheduler
            and int(self.config.shard_count or 1) > 1
            and self.config.compute_state_dir
        )

    def _run_full_eval_with_scheduler(
        self,
        requests: list[FormulaEvalRequest],
        data_dir: str,
        campaign,
        eval_dir: Path,
    ) -> tuple[list[dict], dict[str, Any]]:
        eval_dir.mkdir(parents=True, exist_ok=True)
        request_payloads = [request.to_dict() for request in requests]
        requests_json = eval_dir / "alpha_full_eval_requests.json"
        requests_json.write_text(json.dumps({"requests": request_payloads}, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        self.paths["alpha_full_eval_requests_json_path"] = str(requests_json)
        if not self.config.production_research:
            requests_jsonl = eval_dir / "alpha_full_eval_requests.jsonl"
            with requests_jsonl.open("w", encoding="utf-8") as handle:
                for row in request_payloads:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            self.paths["alpha_full_eval_requests_jsonl_path"] = str(requests_jsonl)

        shard_count = max(1, int(self.config.shard_count or 1))
        shard_root = eval_dir / "shards"
        compute_output_dir = Path(self.config.compute_output_dir) if self.config.compute_output_dir else eval_dir / "compute"
        scheduler = LocalComputeScheduler(
            ComputeSchedulerConfig(
                state_dir=str(self.config.compute_state_dir),
                output_dir=str(compute_output_dir),
                max_parallel_cpu_jobs=max(1, int(self.config.max_parallel_cpu_jobs or 1)),
                max_parallel_gpu_jobs=max(0, int(self.config.max_parallel_gpu_jobs or 0)),
                resume=bool(self.config.resume),
            )
        )
        jobs: list[ComputeJobSpec] = []
        shard_output_dirs: list[Path] = []
        for shard_id in range(shard_count):
            shard_dir = shard_root / f"shard_{shard_id:04d}"
            shard_factor_store = shard_dir / "factor_store"
            shard_report_dir = shard_dir / "reports"
            shard_output_dir = shard_dir / "output"
            shard_output_dir.mkdir(parents=True, exist_ok=True)
            shard_output_dirs.append(shard_output_dir)
            self.paths[f"formula_batch_eval_shard_{shard_id:04d}_output_dir"] = str(shard_output_dir)
            self.paths[f"formula_batch_eval_shard_{shard_id:04d}_result_path"] = str(shard_output_dir / "formula_batch_eval_result.json")
            self.paths[f"formula_batch_eval_shard_{shard_id:04d}_results_path"] = str(shard_output_dir / "formula_eval_results.jsonl")
            self.paths[f"formula_batch_eval_shard_{shard_id:04d}_resource_report_path"] = str(shard_output_dir / "resource_usage.json")
            self.paths[f"formula_batch_eval_shard_{shard_id:04d}_manifest_path"] = str(shard_output_dir / "shard_manifest.json")
            device_arg = "cuda" if str(self.config.batch_eval_device or "").startswith("cuda") else (
                str(self.config.batch_eval_device) if str(self.config.batch_eval_device or "") not in {"", "auto"} else "cpu"
            )
            required_device = ComputeDeviceType.CUDA if device_arg.startswith("cuda") else ComputeDeviceType.CPU
            args = [
                "--requests-json",
                str(requests_json),
                "--data-dir",
                str(data_dir),
                "--factor-store-dir",
                str(shard_factor_store),
                "--report-dir",
                str(shard_report_dir),
                "--output-dir",
                str(shard_output_dir),
                "--shard-id",
                str(shard_id),
                "--shard-count",
                str(shard_count),
                "--write-shard-manifest",
                "--resource-report-path",
                str(shard_output_dir / "resource_usage.json"),
                "--chunk-size",
                str(max(1, int(self.config.batch_eval_chunk_size or 1))),
                "--factor-transform",
                self.config.factor_transform,
                "--correlation-threshold",
                str(float(self.config.correlation_threshold)),
                "--min-coverage",
                str(float(self.config.min_coverage)),
                "--feature-set-name",
                self.config.feature_set_name,
                "--alpha-campaign-id",
                campaign.campaign_id,
                "--device",
                device_arg,
                "--continue-on-error",
            ]
            if self.config.enable_gate:
                args.append("--enable-gate")
            else:
                args.append("--disable-gate")
            if self.config.production_research:
                args.extend(
                    [
                        "--production-research",
                        "--strict-device",
                        "--canonical-feature-tensor-path",
                        str(self.config.canonical_feature_tensor_path),
                        "--canonical-feature-validity-tensor-path",
                        str(self.config.canonical_feature_validity_tensor_path),
                    ]
                )
            if self.config.universe_name:
                args.extend(["--universe-name", self.config.universe_name])
            if self.config.universe_file:
                args.extend(["--universe-file", self.config.universe_file])
            manifest_path = self.config.feature_set_manifest_path or self.paths.get("feature_set_manifest_path")
            if manifest_path:
                args.extend(["--feature-set-manifest-path", manifest_path])
            promotion_hash = _promotion_policy_hash(self.config.feature_promotion_allowlist_path, self.config.feature_promotion_policy_path)
            if promotion_hash:
                args.extend(["--feature-promotion-policy-hash", promotion_hash])
            if self.config.matrix_cache_dir and any((Path(self.config.matrix_cache_dir) / name).exists() for name in ("task_052a_strict_matrix_manifest.json", "metadata.json")):
                args.extend(["--matrix-cache-dir", self.config.matrix_cache_dir, "--use-matrix-cache"])
            args.extend(
                [
                    "--research-end-date",
                    str(self.config.research_end_date or "20240530"),
                    "--holdout-start-date",
                    str(self.config.holdout_start_date or "20240531"),
                    "--label-horizon",
                    str(int(self.config.label_horizon or 2)),
                ]
            )
            if self.config.use_eval_cache:
                args.extend(["--use-eval-cache", "--eval-cache-dir", str(shard_dir / "eval_cache")])
            jobs.append(
                ComputeJobSpec(
                    job_id=f"{campaign.campaign_id}_formula_batch_eval_shard_{shard_id:04d}",
                    job_kind=ComputeJobKind.FORMULA_BATCH_EVAL,
                    command=[sys.executable, "-m", "formula_batch_eval.run_batch_eval"],
                    args=args,
                    cwd=str(Path.cwd()),
                    input_paths=[str(requests_json), str(data_dir)],
                    output_dir=str(shard_output_dir),
                    artifact_paths={
                        "formula_batch_eval_result": str(shard_output_dir / "formula_batch_eval_result.json"),
                        "formula_eval_results": str(shard_output_dir / "formula_eval_results.jsonl"),
                        "resource_usage": str(shard_output_dir / "resource_usage.json"),
                        "shard_manifest": str(shard_output_dir / "shard_manifest.json"),
                    },
                    required_device_type=required_device,
                    gpu_count=1 if required_device == ComputeDeviceType.CUDA else 0,
                    max_retries=0,
                    shard_id=shard_id,
                    shard_count=shard_count,
                    metadata={"alpha_campaign_id": campaign.campaign_id, "formula_requests": len(requests)},
                )
            )
        scheduler.submit_jobs(jobs)
        compute_report = scheduler.run()
        self.paths["compute_run_report_path"] = compute_report.paths.get("compute_run_report", str(compute_output_dir / "compute_run_report.json"))
        self.paths["compute_job_runs_path"] = compute_report.paths.get("compute_job_runs", str(compute_output_dir / "compute_job_runs.jsonl"))
        self.paths["compute_jobs_path"] = compute_report.paths.get("compute_jobs", str(compute_output_dir / "compute_jobs.jsonl"))

        merged_dir = eval_dir / "merged"
        merged = merge_shard_outputs(shard_output_dirs, merged_dir)
        rows = [row for row in merged.get("results", []) if isinstance(row, dict)]
        self.paths["formula_batch_eval_result_path"] = str(merged.get("paths", {}).get("formula_batch_eval_result_path", merged_dir / "formula_batch_eval_result.json"))
        self.paths["formula_eval_results_path"] = str(merged.get("paths", {}).get("formula_eval_results_path", merged_dir / "formula_eval_results.jsonl"))
        self.paths["formula_batch_eval_merged_dir"] = str(merged_dir)

        summary = dict(merged.get("summary") or {})
        summary.update(
            {
                "enabled": True,
                "evaluated": len(rows),
                "batch_id": merged.get("batch_id"),
                "scheduler_enabled": True,
                "scheduler_status": compute_report.status,
                "compute_job_count": int(compute_report.job_count),
                "compute_success_count": int(compute_report.success_count),
                "compute_failed_count": int(compute_report.failed_count),
                "compute_skipped_count": int(compute_report.skipped_count),
                "compute_run_report_path": self.paths.get("compute_run_report_path"),
                "formula_batch_eval_result_path": self.paths.get("formula_batch_eval_result_path"),
            }
        )
        warnings = []
        if compute_report.status != "success" or int(compute_report.success_count) < int(compute_report.job_count):
            warnings.append(
                f"compute scheduler incomplete: status={compute_report.status}, success={compute_report.success_count}/{compute_report.job_count}"
            )
        self.warnings.extend(warnings)
        if warnings:
            summary["warnings"] = warnings
        self.paths["alpha_full_eval_summary_path"] = str(
            write_json_artifact(self.output_dir / "alpha_full_eval_summary.json", summary, "alpha_full_eval_summary", "alpha_factory")
        )
        return rows, summary

    def _register_shortlist_metadata(self, shortlist, full_rows, campaign_id: str) -> int:
        rows_by_candidate = {
            str(row.get("alpha_candidate_id") or (row.get("request") or {}).get("name")): row
            for row in full_rows
            if isinstance(row, dict)
        }
        registered = 0
        created_at = _utc_now()
        for candidate in shortlist:
            row = rows_by_candidate.get(candidate.alpha_candidate_id)
            request = row.get("request") if isinstance(row, dict) else None
            if not isinstance(request, dict):
                continue
            formula_hash = str(request.get("formula_hash") or candidate.formula_hash)
            if self.store.find_factor_by_hash(formula_hash) is not None:
                continue
            metrics_by_split = row.get("metrics_by_split") if isinstance(row.get("metrics_by_split"), dict) else {}
            metadata = {
                "alpha_campaign_id": campaign_id,
                "alpha_candidate_id": candidate.alpha_candidate_id,
                "alpha_family_tags": candidate.family_tags,
                "proxy_score": candidate.proxy_score,
                "full_eval_score": candidate.full_eval_score,
                "novelty_score": candidate.novelty_score,
                "final_score": candidate.final_score,
                "score_components": candidate.metadata.get("score_components") or {},
                "score_method": "dimensionless_cohort_multi_objective_v1",
                "metrics_by_split": metrics_by_split,
                "gate_decision": row.get("gate_decision"),
                "max_abs_correlation": float(row.get("max_abs_correlation", 0.0) or 0.0),
                "factor_values_materialized": False,
                "registration_mode": "shortlist_metadata_only",
                "canonical_semantics_hash": candidate.metadata.get("canonical_semantics_hash"),
                "feature_semantics_contract_hash": candidate.metadata.get("feature_semantics_contract_hash"),
                "canonical_max_raw_lag": candidate.metadata.get("canonical_max_raw_lag"),
                "required_observations": candidate.metadata.get("required_observations"),
            }
            status = str(row.get("status") or "research_evaluated")
            self.store.save_factor(
                FactorRecord(
                    factor_id=str(row.get("factor_id") or make_factor_id(formula_hash)),
                    formula=list(request.get("formula_names") or candidate.formula_names),
                    formula_tokens=list(request.get("formula_tokens") or candidate.formula_tokens),
                    formula_hash=formula_hash,
                    feature_version=candidate.feature_version,
                    operator_version=candidate.operator_version,
                    lookback_days=int(request.get("lookback") or candidate.lookback),
                    created_at=created_at,
                    status=status,
                    description=request.get("description"),
                    metrics=metrics_by_split.get("all") if isinstance(metrics_by_split.get("all"), dict) else {},
                    transform_method=self.config.factor_transform,
                    gate_status=status,
                    gate_reasons=list(row.get("gate_reasons") or []),
                    metadata=metadata,
                    factor_type="single",
                    batch_id=campaign_id,
                )
            )
            registered += 1
        return registered

    def _summary(
        self,
        candidates,
        static_rows,
        proxy_summary,
        full_summary,
        shortlist,
        diversity_report,
        manifest,
        campaign_id: str,
    ) -> dict[str, Any]:
        static_passed = sum(1 for row in static_rows if row.get("status") == "passed")
        proxy_passed = int(proxy_summary.get("passed", 0) or 0)
        best_score = max([item.final_score for item in shortlist], default=0.0)
        source_counts: dict[str, int] = {}
        family_counts: dict[str, int] = {}
        for item in candidates:
            source_counts[item.source] = source_counts.get(item.source, 0) + 1
            for family in item.family_tags:
                family_counts[family] = family_counts.get(family, 0) + 1
        unique_hashes = {item.formula_hash for item in candidates if item.formula_hash}
        scores = [float(item.final_score) for item in candidates]
        proxy_scores = [float(item.proxy_score) for item in candidates]
        return {
            "alpha_factory_enabled": True,
            "alpha_campaign_id": campaign_id,
            "total_trials": len(candidates),
            "evaluated_trials": int(full_summary.get("evaluated", 0) or proxy_passed),
            "selected_trials": len(shortlist),
            "unique_formula_hash_count": len(unique_hashes),
            "generator_attempts": len(candidates),
            "candidates_generated": len(candidates),
            "static_passed": static_passed,
            "static_error_count": len(static_rows) - static_passed,
            "proxy_passed": proxy_passed,
            "full_eval_count": int(full_summary.get("evaluated", 0) or 0),
            "shortlist_count": len(shortlist),
            "shortlisted_candidate_ids": [item.alpha_candidate_id for item in shortlist],
            "best_score": float(best_score),
            "score_distribution": _distribution(scores),
            "proxy_score_distribution": _distribution(proxy_scores),
            "research_policy_id": self.research_policy.policy_id,
            "research_policy_hash": self.research_policy.policy_hash,
            "score_method": "dimensionless_cohort_multi_objective_v1",
            "proxy_normalization_reference_hash": (proxy_summary.get("normalization") or {}).get("reference_hash"),
            "full_normalization_reference_hash": (full_summary.get("normalization") or {}).get("reference_hash"),
            "multiple_testing": full_summary.get("multiple_testing") or {},
            "selection_bias": full_summary.get("selection_bias") or {},
            "selection_data_reused": True,
            "untouched_holdout": False,
            "certification_ready": False,
            "source_budgets": {
                "template": self.config.template_budget,
                "random": self.config.random_budget,
                "mutation": self.config.mutation_budget,
                "crossover": self.config.crossover_budget,
                "corpus": self.config.corpus_budget,
                "neural": self.config.neural_budget,
            },
            "feature_set_name": manifest.feature_set_name,
            "feature_count": manifest.feature_count,
            "promotion_policy_hash": _promotion_policy_hash(
                self.config.feature_promotion_allowlist_path,
                self.config.feature_promotion_policy_path,
            ),
            "require_feature_promotion": bool(self.config.require_feature_promotion),
            "alpha_eligible_feature_count": _allowlist_count(self.config.feature_promotion_allowlist_path, "alpha_eligible_features"),
            "blocked_feature_count": _allowlist_count(self.config.feature_promotion_denylist_path, "blocked_features"),
            "risk_filter_feature_count": _allowlist_count(self.config.feature_promotion_allowlist_path, "risk_filter_only_features"),
            "rejected_by_promotion_count": sum(
                any("feature_used" in str(error) or "feature_promotion" in str(error) for error in row.get("errors", []))
                for row in static_rows
            ),
            "family_distribution": family_counts,
            "source_distribution": source_counts,
            "diversity": diversity_report,
            "compute_run_report_path": self.paths.get("compute_run_report_path"),
        }


def _candidate_from_dict(payload: dict[str, Any]):
    from .models import AlphaCandidateRecord

    allowed = AlphaCandidateRecord.__dataclass_fields__.keys()
    return AlphaCandidateRecord(**{key: payload.get(key) for key in allowed})


def _campaign_id(name: str, created_at: str, seed: int) -> str:
    digest = hashlib.sha256(f"{name}|{created_at}|{seed}".encode("utf-8")).hexdigest()[:12]
    safe = "".join(char if char.isalnum() else "_" for char in name).strip("_") or "campaign"
    return f"alpha_{safe}_{digest}"


def _resolve_data_dir(
    data_dir: str,
    data_freeze_dir: str | None,
    canonical_research_view_manifest_path: str | None = None,
    *,
    production_research: bool = False,
) -> str:
    if production_research:
        if not canonical_research_view_manifest_path:
            raise RuntimeError("production research blocked: physical_research_view_required")
        from data_lake.canonical_freeze import validate_physical_research_view

        view = validate_physical_research_view(canonical_research_view_manifest_path)
        return str(Path(view["view_root"]) / "data")
    if not data_freeze_dir:
        return data_dir
    freeze_root = Path(data_freeze_dir)
    physical_data_dir = freeze_root / "data"
    if physical_data_dir.exists():
        return str(physical_data_dir)
    manifest_path = freeze_root / "freeze_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_data_dir = manifest.get("source_data_dir")
        if source_data_dir and Path(source_data_dir).exists():
            return str(Path(source_data_dir))
    return data_dir


def _file_hash(path: str | None) -> str | None:
    if not path or not Path(path).exists():
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _allowlist_count(path: str | None, key: str) -> int:
    if not path or not Path(path).exists():
        return 0
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    return len(payload.get(key, []) or [])


def _promotion_policy_hash(allowlist_path: str | None, policy_path: str | None) -> str | None:
    for path in (allowlist_path, policy_path):
        if not path or not Path(path).exists():
            continue
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        value = payload.get("policy_hash")
        if value:
            return str(value)
    return _file_hash(policy_path)


def _feature_meta(manifest) -> dict[str, dict[str, Any]]:
    payload = manifest.to_dict() if hasattr(manifest, "to_dict") else dict(manifest)
    return {
        str(item.get("feature_name")): dict(item)
        for item in payload.get("feature_definitions", [])
        if isinstance(item, dict) and item.get("feature_name")
    }


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _alpha_factory_readiness(path: str | None) -> dict[str, Any]:
    if not path:
        return {"ready": True, "status": "not_required", "path": None}
    target = Path(path)
    if not target.exists():
        return {"ready": False, "status": "missing", "path": str(target)}
    payload = json.loads(target.read_text(encoding="utf-8"))
    ready = _truthy(payload.get("can_run_core_alpha_factory")) or _truthy(payload.get("can_run_expanded_alpha_factory"))
    ready = ready or _truthy(payload.get("can_run_v3_expanded_alpha_factory"))
    ready = ready or _truthy(payload.get("alpha_ready"))
    status = str(payload.get("status", "") or "")
    ready = ready or status in {"alpha_factory_ready", "ready_for_alpha_factory", "ready", "pass"}
    return {
        "ready": bool(ready),
        "status": status,
        "path": str(target),
        "can_run_core_alpha_factory": bool(_truthy(payload.get("can_run_core_alpha_factory"))),
        "can_run_expanded_alpha_factory": bool(_truthy(payload.get("can_run_expanded_alpha_factory"))),
        "can_run_v3_expanded_alpha_factory": bool(_truthy(payload.get("can_run_v3_expanded_alpha_factory"))),
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "ready", "pass", "ok"}
    return False


def _validate_production_research_config(config: AlphaCampaignConfig) -> None:
    if not config.production_research:
        return
    blockers: list[str] = []
    if str(config.provider).lower() in {"sample", "fake", "synthetic"} or "lenient" in str(config.provider).lower():
        blockers.append("sample_or_lenient_provider_forbidden")
    if not str(config.device).startswith("cuda"):
        blockers.append("cuda_device_required")
    if not config.use_batch_eval or not str(config.batch_eval_device).startswith("cuda"):
        blockers.append("cuda_batch_evaluation_required")
    if not config.point_in_time:
        blockers.append("point_in_time_required")
    if not config.require_data_freeze or not config.data_freeze_dir:
        blockers.append("immutable_data_freeze_required")
    canonical_manifest = (
        Path(config.data_freeze_dir) / "canonical_freeze_manifest.json"
        if config.data_freeze_dir
        else None
    )
    if canonical_manifest is None or not canonical_manifest.is_file():
        blockers.append("canonical_freeze_manifest_required")
    if not config.canonical_research_view_manifest_path:
        blockers.append("physical_research_view_required")
    elif canonical_manifest is not None and canonical_manifest.is_file():
        try:
            from data_lake.canonical_freeze import (
                validate_canonical_research_freeze,
                validate_physical_research_view,
            )

            freeze = validate_canonical_research_freeze(canonical_manifest)
            view = validate_physical_research_view(config.canonical_research_view_manifest_path)
            if view.get("freeze_content_hash") != freeze.get("content_hash"):
                blockers.append("physical_research_view_freeze_lineage_mismatch")
            if not freeze.get("alpha_search_authorized") or not view.get("alpha_search_authorized"):
                blockers.append("canonical_freeze_research_gate_blocked")
            research_end = str(config.research_end_date or "")
            if not research_end or research_end > str(view.get("max_availability_date") or ""):
                blockers.append("research_cutoff_exceeds_physical_view")
            view_root = Path(str(view["view_root"])).resolve()
            for raw_path in (
                config.matrix_cache_dir,
                config.feature_set_manifest_path,
                config.canonical_feature_tensor_path,
                config.canonical_feature_validity_tensor_path,
            ):
                if raw_path and not Path(raw_path).resolve().is_relative_to(view_root):
                    blockers.append("derived_research_artifact_outside_physical_view")
                    break
        except Exception as exc:
            blockers.append(f"canonical_freeze_validation_failed:{type(exc).__name__}")
    if not config.matrix_cache_dir or not (Path(config.matrix_cache_dir) / "task_052a_strict_matrix_manifest.json").is_file():
        blockers.append("strict_matrix_manifest_required")
    if not config.feature_set_manifest_path:
        blockers.append("feature_manifest_required")
    if not config.canonical_feature_tensor_path or not config.canonical_feature_validity_tensor_path:
        blockers.append("canonical_tensor_and_validity_required")
    if config.build_feature_set:
        blockers.append("runtime_feature_rebuild_forbidden")
    if not config.enable_gate:
        blockers.append("positive_oos_gate_required")
    if config.research_policy_id not in {None, "alpha_factory_two_stage_oos_v1"}:
        blockers.append("production_two_stage_policy_required")
    if int(config.label_horizon) < 1:
        blockers.append("positive_label_horizon_required")
    if blockers:
        raise RuntimeError("production research blocked: " + ",".join(blockers))


def _distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0.0, "min": 0.0, "median": 0.0, "max": 0.0}
    ordered = sorted(values)
    return {
        "count": float(len(values)),
        "min": float(ordered[0]),
        "median": float(ordered[len(ordered) // 2]),
        "max": float(ordered[-1]),
    }


def _proxy_context_hash(candidates, novelty: dict[str, float], reference_root: str, policy_hash: str) -> str:
    payload = {
        "candidates": [
            {
                "alpha_candidate_id": item.alpha_candidate_id,
                "formula_hash": item.formula_hash,
                "formula_tokens": item.formula_tokens,
                "formula_names": item.formula_names,
                "lookback": item.lookback,
                "complexity": item.complexity,
            }
            for item in candidates
        ],
        "novelty": novelty,
        "reference_root": reference_root,
        "policy_hash": policy_hash,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
