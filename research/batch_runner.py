"""Batch factor research orchestration."""

from __future__ import annotations

import json
import contextlib
import io
from datetime import datetime
from pathlib import Path
from typing import Any

from evaluation import build_factor_report, split_trade_dates, write_factor_report
from factor_engine import FactorGateConfig, FactorResearchPipeline, pairwise_correlation_table
from factor_store import (
    ExperimentRecord,
    FactorRecord,
    LocalFactorStore,
    make_experiment_id,
    make_factor_id,
    stable_formula_hash,
)
from model_core.backtest import AShareFactorEvaluator
from model_core.data_loader import AShareDataLoader
from model_core.vm import StackVM

from .candidates import default_candidates
from .composite import build_composite_factor_matrix, register_composite_factor, select_approved_factors
from .models import BatchResearchConfig, BatchResearchResult, CandidateRunResult, FactorCandidate
from .report import write_batch_report


FEATURE_VERSION = "ashare_features_v1"
OPERATOR_VERSION = "ashare_ops_v1"


class BatchFactorResearchRunner:
    def __init__(
        self,
        config: BatchResearchConfig,
        candidates: list[FactorCandidate] | None = None,
    ):
        self.config = config
        self.candidates = candidates or default_candidates()
        self.store = LocalFactorStore(config.factor_store_dir)
        self.loader = AShareDataLoader(
            data_dir=config.data_dir,
            device="cpu",
            universe_name=config.universe_name,
            universe_file=config.universe_file,
            matrix_cache_dir=config.matrix_cache_dir,
            use_matrix_cache=config.use_matrix_cache,
            point_in_time=config.point_in_time,
            feature_cutoff_mode=config.feature_cutoff_mode,
            min_listing_days=config.min_listing_days,
            exclude_st=config.exclude_st,
            corporate_action_aware=config.corporate_action_aware,
            target_return_mode=config.target_return_mode,
            corporate_action_dir=config.corporate_action_dir,
            corporate_action_cash_field=config.corporate_action_cash_field,
        )
        self.vm = StackVM()
        self.evaluator = AShareFactorEvaluator()

    def run(self) -> BatchResearchResult:
        created_at = _utc_now()
        batch_id = self.config.batch_id or _make_batch_id(created_at)
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        if self.config.use_batch_eval:
            return self._run_with_batch_eval(batch_id, created_at, output_dir)
        self.loader.load_data()

        results: list[CandidateRunResult] = []
        for candidate in self.candidates:
            try:
                results.append(self._run_candidate(candidate, batch_id, created_at))
            except Exception as exc:
                if not self.config.continue_on_error:
                    raise
                results.append(
                    CandidateRunResult(
                        candidate=candidate,
                        factor_id=None,
                        status="error",
                        metrics_by_split={},
                        score=0.0,
                        gate_reasons=[str(exc)],
                        max_abs_correlation=0.0,
                        error=str(exc),
                    )
                )

        approved_ids = [
            result.factor_id
            for result in results
            if result.factor_id is not None and result.status == "approved"
        ]
        rejected_ids = [
            result.factor_id
            for result in results
            if result.factor_id is not None and result.status == "rejected"
        ]
        composite_info: dict[str, Any] | None = None
        if not self.config.disable_composite:
            composite_info = self._build_composite(batch_id, created_at)
        leakage_summary, leakage_paths = self._run_leakage_audit_if_requested(output_dir)

        paths = {
            "batch_result_path": str(output_dir / "batch_result.json"),
            "batch_results_path": str(output_dir / "batch_results.jsonl"),
            "batch_report_json_path": str(output_dir / "batch_report.json"),
            "batch_report_md_path": str(output_dir / "batch_report.md"),
        } | leakage_paths
        summary = self._summary(results, composite_info) | {"leakage_audit": leakage_summary}
        batch_result = BatchResearchResult(
            batch_id=batch_id,
            created_at=created_at,
            results=results,
            approved_factor_ids=approved_ids,
            rejected_factor_ids=rejected_ids,
            composite_factor_id=composite_info.get("factor_id") if composite_info else None,
            paths=paths,
            summary=summary,
        )
        self._write_outputs(batch_result)
        write_batch_report(batch_result, output_dir)
        if self.config.fail_on_leakage_blocker and int(leakage_summary.get("blocker_count", 0) or 0) > 0:
            raise RuntimeError("leakage audit found blocker issues")
        return batch_result

    def _run_with_batch_eval(self, batch_id: str, created_at: str, output_dir: Path) -> BatchResearchResult:
        from formula_batch_eval import FormulaBatchEvalConfig, FormulaBatchEvaluator, requests_from_candidates

        eval_output_dir = Path(self.config.batch_eval_output_dir) if self.config.batch_eval_output_dir else output_dir / "batch_eval"
        eval_result = FormulaBatchEvaluator(
            FormulaBatchEvalConfig(
                data_dir=self.config.data_dir,
                universe_name=self.config.universe_name,
                universe_file=self.config.universe_file,
                factor_store_dir=self.config.factor_store_dir,
                report_dir=self.config.report_dir,
                output_dir=str(eval_output_dir),
                matrix_cache_dir=self.config.matrix_cache_dir,
                use_matrix_cache=self.config.use_matrix_cache,
                device=self.config.batch_eval_device,
                factor_transform=self.config.factor_transform,
                enable_gate=self.config.enable_gate,
                correlation_threshold=self.config.correlation_threshold,
                min_coverage=self.config.min_coverage,
                train_ratio=self.config.train_ratio,
                valid_ratio=self.config.valid_ratio,
                chunk_size=self.config.batch_eval_chunk_size,
                use_eval_cache=self.config.use_eval_cache,
                eval_cache_dir=self.config.eval_cache_dir,
                skip_existing=True,
                register_approved=True,
                batch_id=batch_id,
                continue_on_error=self.config.continue_on_error,
            )
        ).run(requests_from_candidates(self.candidates))
        results = [
            CandidateRunResult(
                candidate=FactorCandidate(
                    name=item.request.name,
                    formula_tokens=item.request.formula_tokens,
                    formula_names=item.request.formula_names,
                    description=item.request.description,
                    formula_hash=item.request.formula_hash,
                    complexity=item.request.complexity,
                    lookback=item.request.lookback,
                    source=item.request.source,
                    parent_hashes=(item.request.metadata or {}).get("parent_hashes"),
                    generation=(item.request.metadata or {}).get("generation"),
                    validation_reason=(item.request.metadata or {}).get("validation_reason"),
                ),
                factor_id=item.factor_id,
                status=item.status,
                metrics_by_split=item.metrics_by_split,
                score=item.score,
                gate_reasons=item.gate_reasons,
                max_abs_correlation=item.max_abs_correlation,
                report_json_path=item.report_json_path,
                report_md_path=item.report_md_path,
                error=item.error,
            )
            for item in eval_result.results
        ]
        approved_ids = [result.factor_id for result in results if result.factor_id is not None and result.status == "approved"]
        rejected_ids = [result.factor_id for result in results if result.factor_id is not None and result.status == "rejected"]
        self.loader = AShareDataLoader(
            data_dir=self.config.data_dir,
            device="cpu",
            universe_name=self.config.universe_name,
            universe_file=self.config.universe_file,
            matrix_cache_dir=self.config.matrix_cache_dir,
            use_matrix_cache=self.config.use_matrix_cache,
            point_in_time=self.config.point_in_time,
            feature_cutoff_mode=self.config.feature_cutoff_mode,
            min_listing_days=self.config.min_listing_days,
            exclude_st=self.config.exclude_st,
            corporate_action_aware=self.config.corporate_action_aware,
            target_return_mode=self.config.target_return_mode,
            corporate_action_dir=self.config.corporate_action_dir,
            corporate_action_cash_field=self.config.corporate_action_cash_field,
        )
        self.loader.load_data()
        composite_info = None if self.config.disable_composite else self._build_composite(batch_id, created_at)
        paths = {
            "batch_result_path": str(output_dir / "batch_result.json"),
            "batch_results_path": str(output_dir / "batch_results.jsonl"),
            "batch_report_json_path": str(output_dir / "batch_report.json"),
            "batch_report_md_path": str(output_dir / "batch_report.md"),
            "formula_batch_eval_result_path": eval_result.paths["formula_batch_eval_result_path"],
            "formula_eval_results_path": eval_result.paths["formula_eval_results_path"],
        }
        batch_result = BatchResearchResult(
            batch_id=batch_id,
            created_at=created_at,
            results=results,
            approved_factor_ids=approved_ids,
            rejected_factor_ids=rejected_ids,
            composite_factor_id=composite_info.get("factor_id") if composite_info else None,
            paths=paths,
            summary=self._summary(results, composite_info) | {"batch_eval": eval_result.summary},
        )
        self._write_outputs(batch_result)
        write_batch_report(batch_result, output_dir)
        return batch_result

    def _run_candidate(
        self,
        candidate: FactorCandidate,
        batch_id: str,
        created_at: str,
    ) -> CandidateRunResult:
        if not self.vm.validate(candidate.formula_tokens):
            raise ValueError(f"candidate {candidate.name} has invalid formula arity")
        formula_hash = stable_formula_hash(
            formula_tokens=candidate.formula_tokens,
            formula_names=candidate.formula_names,
            feature_version=FEATURE_VERSION,
            operator_version=OPERATOR_VERSION,
        )
        formula_hash = candidate.formula_hash or formula_hash
        existing = self.store.find_factor_by_hash(formula_hash)
        if existing is not None:
            return CandidateRunResult(
                candidate=candidate,
                factor_id=existing.factor_id,
                status="skipped_existing",
                metrics_by_split={"all": existing.metrics or {}},
                score=_score(existing.metrics),
                gate_reasons=["skipped_existing"],
                max_abs_correlation=float((existing.metadata or {}).get("max_abs_correlation", 0.0) or 0.0),
            )

        raw_factors = self.vm.execute(candidate.formula_tokens, self.loader.feat_tensor)
        if raw_factors is None:
            raise RuntimeError(f"candidate {candidate.name} failed during formula execution")

        split_result = split_trade_dates(
            self.loader.trade_dates,
            train_ratio=self.config.train_ratio,
            valid_ratio=self.config.valid_ratio,
        )
        gate_config = FactorGateConfig(
            min_coverage=self.config.min_coverage,
            max_abs_correlation=self.config.correlation_threshold,
        )
        research = FactorResearchPipeline(
            evaluator=self.evaluator,
            gate_config=gate_config,
            enable_gate=self.config.enable_gate,
            correlation_threshold=self.config.correlation_threshold,
        ).run(
            factors=raw_factors,
            raw_data=self.loader.raw_data_cache,
            target_ret=self.loader.target_ret,
            trade_dates=self.loader.trade_dates,
            ts_codes=self.loader.ts_codes,
            store=self.store,
            transform_method=self.config.factor_transform,
            train_ratio=self.config.train_ratio,
            valid_ratio=self.config.valid_ratio,
        )

        factor_id = make_factor_id(formula_hash)
        experiment_id = make_experiment_id(factor_id, created_at)
        gate_payload = research.gate_decision.to_dict() if research.gate_decision is not None else None
        gate_reasons = research.gate_decision.reasons if research.gate_decision is not None else []
        record = FactorRecord(
            factor_id=factor_id,
            formula=candidate.formula_names,
            formula_tokens=candidate.formula_tokens,
            formula_hash=formula_hash,
            feature_version=FEATURE_VERSION,
            operator_version=OPERATOR_VERSION,
            lookback_days=int(candidate.lookback or _estimate_lookback_days(candidate.formula_names)),
            created_at=created_at,
            status=research.status,
            description=candidate.description,
            metrics=research.metrics_by_split.get("all", {}),
            transform_method=research.transform_method,
            gate_status=research.gate_decision.status if research.gate_decision is not None else None,
            gate_reasons=gate_reasons or None,
            metadata={
                "candidate_name": candidate.name,
                "formula_complexity": candidate.complexity,
                "formula_lookback": candidate.lookback,
                "formula_source": candidate.source,
                "parent_hashes": candidate.parent_hashes,
                "generation": candidate.generation,
                "search_id": self.config.search_id,
                "max_abs_correlation": float(research.max_abs_correlation),
                "similar_factors": research.similar_factors,
                "gate_decision": gate_payload,
                "batch_id": batch_id,
                "universe_name": self.config.universe_name,
                "universe_file": self.config.universe_file,
                "point_in_time": self.config.point_in_time,
                "feature_cutoff_mode": self.config.feature_cutoff_mode,
                "pit_contract_version": "1.0" if self.config.point_in_time else None,
                "active_mask_applied": self.config.point_in_time,
                "corporate_action_aware": self.config.corporate_action_aware,
                "target_return_mode": self.config.target_return_mode,
                "total_return_mode": self.config.target_return_mode,
                "corporate_action_dir": self.config.corporate_action_dir,
                "corporate_action_cash_field": self.config.corporate_action_cash_field,
                "corporate_action_event_count": int(
                    self.loader.raw_data_cache.get("corporate_action_flag").sum().item()
                    if self.loader.raw_data_cache.get("corporate_action_flag") is not None
                    else 0
                ),
            },
            factor_type="single",
            batch_id=batch_id,
        )
        experiment = ExperimentRecord(
            experiment_id=experiment_id,
            factor_id=factor_id,
            data_dir=self.config.data_dir,
            output_dir=self.config.output_dir,
            train_dates=split_result.train_dates,
            valid_dates=split_result.valid_dates,
            test_dates=split_result.test_dates,
            metrics_by_split=research.metrics_by_split,
            created_at=created_at,
            notes=f"batch_id={batch_id}; candidate={candidate.name}",
        )
        self.store.save_factor(record)
        self.store.save_experiment(experiment)
        self.store.save_factor_values(
            factor_id,
            self.loader.ts_codes,
            self.loader.trade_dates,
            research.transformed_factors,
        )

        report = build_factor_report(
            factor_id=factor_id,
            experiment_id=experiment_id,
            formula=candidate.formula_names,
            formula_tokens=candidate.formula_tokens,
            metrics_by_split=research.metrics_by_split,
            n_stocks=len(self.loader.ts_codes),
            n_dates=len(self.loader.trade_dates),
            n_features=int(self.loader.feat_tensor.shape[1]),
            train_dates=split_result.train_dates,
            valid_dates=split_result.valid_dates,
            test_dates=split_result.test_dates,
            created_at=created_at,
            transform_method=research.transform_method,
            gate_decision=gate_payload,
            max_abs_correlation=research.max_abs_correlation,
            similar_factors=research.similar_factors,
            status=research.status,
        )
        report_json, report_md = write_factor_report(report, Path(self.config.report_dir) / "factors" / factor_id)

        return CandidateRunResult(
            candidate=candidate,
            factor_id=factor_id,
            status=research.status,
            metrics_by_split=research.metrics_by_split,
            score=_score(research.metrics_by_split.get("all")),
            gate_reasons=gate_reasons,
            max_abs_correlation=float(research.max_abs_correlation),
            report_json_path=str(report_json),
            report_md_path=str(report_md),
        )

    def _build_composite(self, batch_id: str, created_at: str) -> dict[str, Any] | None:
        factor_ids = select_approved_factors(
            self.store,
            max_factors=max(self.config.top_k, 0),
            max_pairwise_corr=self.config.correlation_threshold,
        )
        if not factor_ids:
            return None
        component_matrices = {
            factor_id: self.store.load_factor_values_matrix(
                factor_id,
                ts_codes=self.loader.ts_codes,
                trade_dates=self.loader.trade_dates,
                device="cpu",
            )
            for factor_id in factor_ids
        }
        values = build_composite_factor_matrix(
            self.store,
            factor_ids=factor_ids,
            ts_codes=self.loader.ts_codes,
            trade_dates=self.loader.trade_dates,
            method=self.config.composite_method,
        )
        info = register_composite_factor(
            self.store,
            factor_ids=factor_ids,
            ts_codes=self.loader.ts_codes,
            trade_dates=self.loader.trade_dates,
            values=values,
            method=self.config.composite_method,
            batch_id=batch_id,
            created_at=created_at,
        )
        info["pairwise_correlations"] = pairwise_correlation_table(component_matrices)
        return info

    def _write_outputs(self, result: BatchResearchResult) -> None:
        payload = result.to_dict()
        result_path = Path(result.paths["batch_result_path"])
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with Path(result.paths["batch_results_path"]).open("w", encoding="utf-8") as handle:
            for item in result.results:
                handle.write(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")

    def _run_leakage_audit_if_requested(self, output_dir: Path) -> tuple[dict[str, Any], dict[str, str]]:
        if not self.config.run_leakage_audit:
            return {}, {}
        from leakage_audit.run_audit import main as leakage_main

        audit_dir = Path(self.config.leakage_audit_dir) if self.config.leakage_audit_dir else output_dir / "leakage_audit"
        argv = [
            "--data-dir",
            self.config.data_dir,
            "--factor-store-dir",
            self.config.factor_store_dir,
            "--output-dir",
            str(audit_dir),
            "--cutoff-date",
            self.loader.trade_dates[-1] if self.loader.trade_dates else "",
            "--max-formulas",
            "5",
            "--run-static-scan",
            "--run-truncation-test",
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
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            rc = leakage_main(argv)
        report_path = audit_dir / "leakage_audit_report.json"
        payload = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {"return_code": rc}
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        return payload, {name: str(path) for name, path in paths.items()}

    @staticmethod
    def _summary(results: list[CandidateRunResult], composite_info: dict[str, Any] | None) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
        ranked = sorted(results, key=lambda item: item.score, reverse=True)
        return {
            "total_candidates": len(results),
            "status_counts": counts,
            "top_factors": [
                {
                    "candidate": item.candidate.name,
                    "factor_id": item.factor_id,
                    "status": item.status,
                    "score": float(item.score),
                }
                for item in ranked[:10]
            ],
            "composite": composite_info,
        }


def _score(metrics: dict[str, float] | None) -> float:
    if not isinstance(metrics, dict):
        return 0.0
    try:
        return float(metrics.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _estimate_lookback_days(formula_names: list[str]) -> int:
    if "RET_5D" in formula_names:
        return 5
    if any(name in formula_names for name in {"TS_MEAN3", "TS_STD3", "TS_ZSCORE3"}):
        return 3
    return 1


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _make_batch_id(created_at: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in created_at).strip("_")
    return f"batch_{safe}"
