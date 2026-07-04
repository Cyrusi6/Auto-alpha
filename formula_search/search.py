"""Search-style batch factor research runner."""

from __future__ import annotations

import json
import random
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from factor_store import LocalFactorStore
from model_core.data_loader import AShareDataLoader
from research import BatchFactorResearchRunner, BatchResearchConfig
from research.candidates import from_formula_search_candidates
from research.composite import build_composite_factor_matrix, register_composite_factor, select_approved_factors

from .generator import generate_initial_population
from .models import FormulaCandidate, FormulaSearchConfig, FormulaSearchResult
from .mutation import crossover_formula, mutate_formula
from .report import write_search_report


class FormulaSearchRunner:
    def __init__(
        self,
        search_config: FormulaSearchConfig,
        data_dir: str,
        universe_name: str | None,
        universe_file: str | None,
        factor_store_dir: str,
        report_dir: str,
        output_dir: str,
        factor_transform: str = "raw",
        enable_gate: bool = True,
        correlation_threshold: float = 0.95,
        min_coverage: float = 0.8,
        composite_method: str = "rank_average",
        train_ratio: float = 0.6,
        valid_ratio: float = 0.2,
        continue_on_error: bool = True,
        matrix_cache_dir: str | None = None,
        use_matrix_cache: bool = False,
        use_batch_eval: bool = False,
        batch_eval_output_dir: str | None = None,
        batch_eval_chunk_size: int = 32,
        batch_eval_device: str = "auto",
        use_eval_cache: bool = False,
        eval_cache_dir: str | None = None,
        point_in_time: bool = False,
        feature_cutoff_mode: str = "same_day_after_close",
        min_listing_days: int = 0,
        exclude_st: bool = False,
        run_leakage_audit: bool = False,
        leakage_audit_dir: str | None = None,
        fail_on_leakage_blocker: bool = False,
        corporate_action_aware: bool = False,
        target_return_mode: str = "adjusted_close",
        corporate_action_dir: str | None = None,
        corporate_action_cash_field: str = "cash_div",
        data_freeze_dir: str | None = None,
        data_freeze_id: str | None = None,
        data_version_manifest_path: str | None = None,
        require_data_freeze: bool = False,
        freeze_validation_report_path: str | None = None,
        compute_state_dir: str | None = None,
        compute_output_dir: str | None = None,
        use_compute_scheduler: bool = False,
        formula_shard_count: int = 1,
        formula_shard_id: int | None = None,
        resource_report_path: str | None = None,
        experiment_id: str | None = None,
        alpha_candidates_path: str | None = None,
        alpha_seed_top_k: int | None = None,
        alpha_campaign_manifest_path: str | None = None,
        feature_set_name: str = "ashare_features_v1",
        feature_set_manifest_path: str | None = None,
        feature_promotion_policy_path: str | None = None,
        feature_promotion_allowlist_path: str | None = None,
        feature_promotion_denylist_path: str | None = None,
        require_feature_promotion: bool = False,
        allow_risk_filter_features: bool = False,
    ):
        self.search_config = search_config
        self.data_dir = data_dir
        self.universe_name = universe_name
        self.universe_file = universe_file
        self.factor_store_dir = factor_store_dir
        self.report_dir = report_dir
        self.output_dir = Path(output_dir)
        self.factor_transform = factor_transform
        self.enable_gate = enable_gate
        self.correlation_threshold = correlation_threshold
        self.min_coverage = min_coverage
        self.composite_method = composite_method
        self.train_ratio = train_ratio
        self.valid_ratio = valid_ratio
        self.continue_on_error = continue_on_error
        self.matrix_cache_dir = matrix_cache_dir
        self.use_matrix_cache = bool(use_matrix_cache)
        self.use_batch_eval = bool(use_batch_eval)
        self.batch_eval_output_dir = batch_eval_output_dir
        self.batch_eval_chunk_size = int(batch_eval_chunk_size)
        self.batch_eval_device = batch_eval_device
        self.use_eval_cache = bool(use_eval_cache)
        self.eval_cache_dir = eval_cache_dir
        self.point_in_time = bool(point_in_time)
        self.feature_cutoff_mode = feature_cutoff_mode
        self.min_listing_days = int(min_listing_days)
        self.exclude_st = bool(exclude_st)
        self.run_leakage_audit = bool(run_leakage_audit)
        self.leakage_audit_dir = leakage_audit_dir
        self.fail_on_leakage_blocker = bool(fail_on_leakage_blocker)
        self.corporate_action_aware = bool(corporate_action_aware)
        self.target_return_mode = target_return_mode
        self.corporate_action_dir = corporate_action_dir
        self.corporate_action_cash_field = corporate_action_cash_field
        self.data_freeze_dir = data_freeze_dir
        self.data_freeze_id = data_freeze_id
        self.data_version_manifest_path = data_version_manifest_path
        self.require_data_freeze = bool(require_data_freeze)
        self.freeze_validation_report_path = freeze_validation_report_path
        self.compute_state_dir = compute_state_dir
        self.compute_output_dir = compute_output_dir
        self.use_compute_scheduler = bool(use_compute_scheduler)
        self.formula_shard_count = int(formula_shard_count)
        self.formula_shard_id = formula_shard_id
        self.resource_report_path = resource_report_path
        self.experiment_id = experiment_id
        self.alpha_candidates_path = alpha_candidates_path
        self.alpha_seed_top_k = alpha_seed_top_k
        self.alpha_campaign_manifest_path = alpha_campaign_manifest_path
        self.feature_set_name = feature_set_name
        self.feature_set_manifest_path = feature_set_manifest_path
        self.feature_promotion_policy_path = feature_promotion_policy_path
        self.feature_promotion_allowlist_path = feature_promotion_allowlist_path
        self.feature_promotion_denylist_path = feature_promotion_denylist_path
        self.require_feature_promotion = bool(require_feature_promotion)
        self.allow_risk_filter_features = bool(allow_risk_filter_features)
        self.rng = random.Random(search_config.seed)

    def run(self) -> FormulaSearchResult:
        created_at = _utc_now()
        search_id = _make_search_id(created_at, self.search_config.seed)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        population = self._initial_population()
        generated: dict[str, FormulaCandidate] = {candidate.formula_hash: candidate for candidate in population}
        evaluated_hashes: set[str] = set()
        generation_summaries: list[dict[str, Any]] = []
        all_results: list[dict[str, Any]] = []

        for generation in range(max(self.search_config.generations, 0)):
            batch_candidates = [candidate for candidate in population if candidate.formula_hash not in evaluated_hashes]
            batch_size = self.search_config.candidate_batch_size or self.search_config.population_size
            batch_candidates = batch_candidates[: max(batch_size, 0)]
            for candidate in batch_candidates:
                evaluated_hashes.add(candidate.formula_hash)
            batch_result = self._run_generation_batch(search_id, generation, batch_candidates)
            result_payloads = [result.to_dict() for result in batch_result.results]
            all_results.extend(result_payloads)
            generation_summaries.append(
                {
                    "generation": generation,
                    "candidates": len(batch_candidates),
                    "approved": len(batch_result.approved_factor_ids),
                    "rejected": len(batch_result.rejected_factor_ids),
                    "skipped": sum(1 for item in batch_result.results if item.status == "skipped_existing"),
                    "errors": sum(1 for item in batch_result.results if item.status == "error"),
                    "batch_id": batch_result.batch_id,
                }
            )

            elites = self._select_elites(batch_result.results, generated)
            population = self._next_population(elites or population, generated)

        composite_info = self._register_composite(search_id, created_at)
        approved_factor_ids = _unique(
            str(item.get("factor_id"))
            for item in all_results
            if item.get("factor_id") and item.get("status") == "approved"
        )
        best = sorted(all_results, key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)[: self.search_config.top_k]
        paths = {
            "search_result_path": str(self.output_dir / "search_result.json"),
            "search_candidates_path": str(self.output_dir / "search_candidates.jsonl"),
            "search_report_json_path": str(self.output_dir / "search_report.json"),
            "search_report_md_path": str(self.output_dir / "search_report.md"),
        }
        result = FormulaSearchResult(
            search_id=search_id,
            generations=generation_summaries,
            candidates_generated=len(generated),
            candidates_valid=sum(1 for candidate in generated.values() if candidate.validation_reason == "ok"),
            candidates_evaluated=len(all_results),
            approved_factor_ids=approved_factor_ids,
            composite_factor_id=composite_info.get("factor_id") if composite_info else None,
            best_candidates=best,
            paths=paths,
            config=asdict(self.search_config)
            | {
                "data_dir": self.data_dir,
                "universe_name": self.universe_name,
                "universe_file": self.universe_file,
                "factor_store_dir": self.factor_store_dir,
                "report_dir": self.report_dir,
                "output_dir": str(self.output_dir),
                "factor_transform": self.factor_transform,
                "enable_gate": self.enable_gate,
                "correlation_threshold": self.correlation_threshold,
                "min_coverage": self.min_coverage,
                "composite_method": self.composite_method,
                "matrix_cache_dir": self.matrix_cache_dir,
                "use_matrix_cache": self.use_matrix_cache,
                "use_batch_eval": self.use_batch_eval,
                "point_in_time": self.point_in_time,
                "feature_cutoff_mode": self.feature_cutoff_mode,
                "min_listing_days": self.min_listing_days,
                "exclude_st": self.exclude_st,
                "run_leakage_audit": self.run_leakage_audit,
                "corporate_action_aware": self.corporate_action_aware,
                "target_return_mode": self.target_return_mode,
                "corporate_action_dir": self.corporate_action_dir,
                "data_freeze_dir": self.data_freeze_dir,
                "data_freeze_id": self.data_freeze_id,
                "data_version_manifest_path": self.data_version_manifest_path,
                "require_data_freeze": self.require_data_freeze,
                "use_compute_scheduler": self.use_compute_scheduler,
                "formula_shard_count": self.formula_shard_count,
                "formula_shard_id": self.formula_shard_id,
                "resource_report_path": self.resource_report_path,
                "experiment_id": self.experiment_id,
                "alpha_candidates_path": self.alpha_candidates_path,
                "alpha_campaign_manifest_path": self.alpha_campaign_manifest_path,
                "feature_set_name": self.feature_set_name,
                "feature_set_manifest_path": self.feature_set_manifest_path,
                "feature_promotion_policy_path": self.feature_promotion_policy_path,
                "feature_promotion_allowlist_path": self.feature_promotion_allowlist_path,
                "feature_promotion_denylist_path": self.feature_promotion_denylist_path,
                "require_feature_promotion": self.require_feature_promotion,
                "allow_risk_filter_features": self.allow_risk_filter_features,
            },
        )
        self._write_outputs(result, generated)
        write_search_report(result, self.output_dir)
        return result

    def _run_generation_batch(self, search_id: str, generation: int, candidates: list[FormulaCandidate]):
        config = BatchResearchConfig(
            data_dir=self.data_dir,
            universe_name=self.universe_name,
            universe_file=self.universe_file,
            factor_store_dir=self.factor_store_dir,
            report_dir=self.report_dir,
            output_dir=str(self.output_dir / f"generation_{generation}"),
            factor_transform=self.factor_transform,
            enable_gate=self.enable_gate,
            correlation_threshold=self.correlation_threshold,
            min_coverage=self.min_coverage,
            top_k=self.search_config.top_k,
            composite_method=self.composite_method,
            train_ratio=self.train_ratio,
            valid_ratio=self.valid_ratio,
            continue_on_error=self.continue_on_error,
            disable_composite=True,
            batch_id=f"{search_id}_gen_{generation}",
            search_id=search_id,
            matrix_cache_dir=self.matrix_cache_dir,
            use_matrix_cache=self.use_matrix_cache,
            use_batch_eval=self.use_batch_eval,
            batch_eval_output_dir=(
                str(Path(self.batch_eval_output_dir) / f"generation_{generation}")
                if self.batch_eval_output_dir
                else None
            ),
            batch_eval_chunk_size=self.batch_eval_chunk_size,
            batch_eval_device=self.batch_eval_device,
            use_eval_cache=self.use_eval_cache,
            eval_cache_dir=self.eval_cache_dir,
            point_in_time=self.point_in_time,
            feature_cutoff_mode=self.feature_cutoff_mode,
            min_listing_days=self.min_listing_days,
            exclude_st=self.exclude_st,
            run_leakage_audit=self.run_leakage_audit,
            leakage_audit_dir=(
                str(Path(self.leakage_audit_dir) / f"generation_{generation}")
                if self.leakage_audit_dir
                else None
            ),
            fail_on_leakage_blocker=self.fail_on_leakage_blocker,
            corporate_action_aware=self.corporate_action_aware,
            target_return_mode=self.target_return_mode,
            corporate_action_dir=self.corporate_action_dir,
            corporate_action_cash_field=self.corporate_action_cash_field,
            data_freeze_dir=self.data_freeze_dir,
            data_freeze_id=self.data_freeze_id,
            data_version_manifest_path=self.data_version_manifest_path,
            require_data_freeze=self.require_data_freeze,
            freeze_validation_report_path=self.freeze_validation_report_path,
            compute_state_dir=self.compute_state_dir,
            compute_output_dir=self.compute_output_dir,
            use_compute_scheduler=self.use_compute_scheduler,
            formula_shard_count=self.formula_shard_count,
            formula_shard_id=self.formula_shard_id,
            resource_report_path=self.resource_report_path,
            feature_set_name=self.feature_set_name,
            feature_set_manifest_path=self.feature_set_manifest_path,
            feature_promotion_policy_path=self.feature_promotion_policy_path,
            feature_promotion_allowlist_path=self.feature_promotion_allowlist_path,
            feature_promotion_denylist_path=self.feature_promotion_denylist_path,
            require_feature_promotion=self.require_feature_promotion,
            allow_risk_filter_features=self.allow_risk_filter_features,
            alpha_campaign_id=_alpha_campaign_id(self.alpha_campaign_manifest_path),
            alpha_candidates_path=self.alpha_candidates_path,
            alpha_factory_report_path=None,
        )
        return BatchFactorResearchRunner(config=config, candidates=from_formula_search_candidates(candidates)).run()

    def _initial_population(self) -> list[FormulaCandidate]:
        population = generate_initial_population(self.search_config)
        if not self.alpha_candidates_path:
            return population
        alpha = _load_alpha_candidates(self.alpha_candidates_path, self.alpha_seed_top_k)
        merged: dict[str, FormulaCandidate] = {candidate.formula_hash: candidate for candidate in alpha}
        for candidate in population:
            merged.setdefault(candidate.formula_hash, candidate)
        return list(merged.values())[: max(self.search_config.population_size, len(alpha))]

    def _select_elites(self, results, generated: dict[str, FormulaCandidate]) -> list[FormulaCandidate]:
        ranked = sorted(results, key=lambda item: item.score, reverse=True)
        elites: list[FormulaCandidate] = []
        for result in ranked:
            candidate_hash = result.candidate.formula_hash
            if candidate_hash and candidate_hash in generated:
                elites.append(generated[candidate_hash])
            if len(elites) >= max(self.search_config.elite_size, 1):
                break
        return elites

    def _next_population(
        self,
        elites: list[FormulaCandidate],
        generated: dict[str, FormulaCandidate],
    ) -> list[FormulaCandidate]:
        next_population = list(elites[: max(self.search_config.elite_size, 1)])
        attempts = 0
        while len(next_population) < self.search_config.population_size and attempts < self.search_config.population_size * 100:
            attempts += 1
            if len(elites) >= 2 and self.rng.random() < self.search_config.crossover_rate:
                left, right = self.rng.sample(elites, 2)
                child = crossover_formula(left, right, self.rng, self.search_config)
            else:
                parent = self.rng.choice(elites)
                child = mutate_formula(parent, self.rng, self.search_config)
            if child.formula_hash in generated:
                continue
            generated[child.formula_hash] = child
            next_population.append(child)
        return next_population

    def _register_composite(self, search_id: str, created_at: str) -> dict[str, Any] | None:
        store = LocalFactorStore(self.factor_store_dir)
        factor_ids = select_approved_factors(
            store,
            max_factors=max(self.search_config.top_k, 0),
            max_pairwise_corr=self.correlation_threshold,
        )
        if not factor_ids:
            return None
        loader = AShareDataLoader(
            data_dir=self.data_dir,
            device="cpu",
            universe_name=self.universe_name,
            universe_file=self.universe_file,
            matrix_cache_dir=self.matrix_cache_dir,
            use_matrix_cache=self.use_matrix_cache,
            point_in_time=self.point_in_time,
            feature_cutoff_mode=self.feature_cutoff_mode,
            min_listing_days=self.min_listing_days,
            exclude_st=self.exclude_st,
            corporate_action_aware=self.corporate_action_aware,
            target_return_mode=self.target_return_mode,
            corporate_action_dir=self.corporate_action_dir,
        ).load_data()
        values = build_composite_factor_matrix(
            store,
            factor_ids,
            loader.ts_codes,
            loader.trade_dates,
            method=self.composite_method,
        )
        return register_composite_factor(
            store,
            factor_ids,
            loader.ts_codes,
            loader.trade_dates,
            values,
            method=self.composite_method,
            batch_id=search_id,
            created_at=created_at,
        )

    def _write_outputs(self, result: FormulaSearchResult, generated: dict[str, FormulaCandidate]) -> None:
        (self.output_dir / "search_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with (self.output_dir / "search_candidates.jsonl").open("w", encoding="utf-8") as handle:
            for candidate in generated.values():
                handle.write(json.dumps(candidate.to_dict(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _make_search_id(created_at: str, seed: int) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in created_at).strip("_")
    return f"search_{seed}_{safe}"


def _unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _load_alpha_candidates(path: str, top_k: int | None) -> list[FormulaCandidate]:
    target = Path(path)
    if not target.exists():
        return []
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = sorted(rows, key=lambda item: float(item.get("final_score", 0.0) or 0.0), reverse=True)
    if top_k is not None:
        rows = rows[: max(top_k, 0)]
    candidates: list[FormulaCandidate] = []
    for idx, row in enumerate(rows):
        tokens = [int(item) for item in row.get("formula_tokens", [])]
        names = [str(item) for item in row.get("formula_names", [])]
        formula_hash = str(row.get("formula_hash") or f"alpha_{idx}")
        candidates.append(
            FormulaCandidate(
                formula_tokens=tokens,
                formula_names=names,
                formula_hash=formula_hash,
                complexity=int(row.get("complexity", len(tokens)) or len(tokens)),
                lookback=int(row.get("lookback", 0) or 0),
                source=f"alpha_factory:{row.get('source', 'shortlist')}",
                parent_hashes=[str(row.get("alpha_candidate_id", ""))],
                generation=0,
                validation_reason="ok",
            )
        )
    return candidates


def _alpha_campaign_id(path: str | None) -> str | None:
    if not path or not Path(path).exists():
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return payload.get("campaign_id")
    except Exception:
        return None
