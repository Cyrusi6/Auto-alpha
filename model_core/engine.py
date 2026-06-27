"""A-share factor mining engine."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import torch

from evaluation import build_factor_report, split_trade_dates, write_factor_report
from factor_engine import SUPPORTED_TRANSFORMS, FactorGateConfig, FactorResearchPipeline
from factor_store import (
    ExperimentRecord,
    FactorRecord,
    LocalFactorStore,
    make_experiment_id,
    make_factor_id,
    stable_formula_hash,
)
from neural_search.models import NeuralSearchConfig
from neural_search.trainer import NeuralFormulaTrainer

from .backtest import AShareFactorEvaluator, FactorEvaluationResult
from .config import ModelConfig
from .data_loader import AShareDataLoader
from .vm import StackVM
from .vocab import FORMULA_VOCAB


class FactorMiningEngine:
    feature_version = "ashare_features_v1"
    operator_version = "ashare_ops_v1"

    def __init__(
        self,
        data_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        device: torch.device | str | None = None,
        universe_file: str | Path | None = None,
        universe_name: str | None = None,
    ):
        self.data_dir = Path(data_dir) if data_dir is not None else Path(ModelConfig.DATA_DIR)
        self.output_dir = Path(output_dir) if output_dir is not None else Path(ModelConfig.OUTPUT_DIR)
        self.device = torch.device(device) if device is not None else ModelConfig.DEVICE
        self.universe_file = Path(universe_file) if universe_file is not None else None
        self.universe_name = universe_name
        self.loader = AShareDataLoader(
            data_dir=self.data_dir,
            device=self.device,
            universe_file=self.universe_file,
            universe_name=self.universe_name,
        )
        self.vm = StackVM()
        self.evaluator = AShareFactorEvaluator()
        self.best_score = -float("inf")
        self.best_formula: list[int] | None = None
        self.best_metrics: FactorEvaluationResult | None = None
        self.training_history: list[dict[str, object]] = []

    def load_data(self) -> None:
        self.loader.load_data()

    def dry_run(
        self,
        register: bool = False,
        factor_store_dir: str | Path | None = None,
        report_dir: str | Path | None = None,
        train_ratio: float = 0.6,
        valid_ratio: float = 0.2,
        factor_transform: str = "raw",
        enable_gate: bool = False,
        gate_config: FactorGateConfig | None = None,
        correlation_threshold: float = 0.95,
    ) -> dict[str, object]:
        self.load_data()
        formula = [FORMULA_VOCAB.encode_name("RET_1D")]
        factors = self.vm.execute(formula, self.loader.feat_tensor)
        if factors is None:
            raise RuntimeError("failed to execute dry-run formula")
        metrics = self.evaluator.evaluate(factors, self.loader.raw_data_cache, self.loader.target_ret)
        payload = self._summary(formula, metrics)
        if register:
            payload.update(
                self.register_factor(
                    formula=formula,
                    factors=factors,
                    factor_store_dir=factor_store_dir,
                    report_dir=report_dir,
                    train_ratio=train_ratio,
                    valid_ratio=valid_ratio,
                    factor_transform=factor_transform,
                    enable_gate=enable_gate,
                    gate_config=gate_config,
                    correlation_threshold=correlation_threshold,
                )
            )
        return payload

    def train(
        self,
        steps: int | None = None,
        batch_size: int | None = None,
        register: bool = True,
        factor_store_dir: str | Path | None = None,
        report_dir: str | Path | None = None,
        train_ratio: float = 0.6,
        valid_ratio: float = 0.2,
        factor_transform: str = "raw",
        enable_gate: bool = False,
        gate_config: FactorGateConfig | None = None,
        correlation_threshold: float = 0.95,
    ) -> dict[str, object]:
        self.load_data()
        steps = int(steps if steps is not None else ModelConfig.TRAIN_STEPS)
        batch_size = int(batch_size if batch_size is not None else ModelConfig.BATCH_SIZE)

        candidates = self._candidate_formulas()
        for step in range(max(steps, 0)):
            step_scores: list[float] = []
            for idx in range(max(batch_size, 1)):
                formula = candidates[(step * max(batch_size, 1) + idx) % len(candidates)]
                factors = self.vm.execute(formula, self.loader.feat_tensor)
                if factors is None:
                    continue
                metrics = self.evaluator.evaluate(factors, self.loader.raw_data_cache, self.loader.target_ret)
                step_scores.append(metrics.score)
                if metrics.score > self.best_score:
                    self.best_score = metrics.score
                    self.best_formula = formula
                    self.best_metrics = metrics
            self.training_history.append(
                {
                    "step": step,
                    "avg_score": float(sum(step_scores) / len(step_scores)) if step_scores else 0.0,
                    "best_score": float(self.best_score if self.best_metrics is not None else 0.0),
                }
            )

        if self.best_formula is None or self.best_metrics is None:
            self.best_formula = [FORMULA_VOCAB.encode_name("RET_1D")]
            factors = self.vm.execute(self.best_formula, self.loader.feat_tensor)
            if factors is None:
                raise RuntimeError("failed to execute fallback formula")
            self.best_metrics = self.evaluator.evaluate(factors, self.loader.raw_data_cache, self.loader.target_ret)
            self.best_score = self.best_metrics.score

        self.output_dir.mkdir(parents=True, exist_ok=True)
        best_payload = self._summary(self.best_formula, self.best_metrics)
        (self.output_dir / "best_factor_formula.json").write_text(
            json.dumps(best_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.output_dir / "training_history.json").write_text(
            json.dumps(self.training_history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if register:
            factors = self.vm.execute(self.best_formula, self.loader.feat_tensor)
            if factors is None:
                raise RuntimeError("failed to execute selected formula")
            best_payload.update(
                self.register_factor(
                    formula=self.best_formula,
                    factors=factors,
                    factor_store_dir=factor_store_dir,
                    report_dir=report_dir,
                    train_ratio=train_ratio,
                    valid_ratio=valid_ratio,
                    factor_transform=factor_transform,
                    enable_gate=enable_gate,
                    gate_config=gate_config,
                    correlation_threshold=correlation_threshold,
                )
            )
        return best_payload

    def register_factor(
        self,
        formula: list[int],
        factors: torch.Tensor,
        factor_store_dir: str | Path | None = None,
        report_dir: str | Path | None = None,
        train_ratio: float = 0.6,
        valid_ratio: float = 0.2,
        factor_transform: str = "raw",
        enable_gate: bool = False,
        gate_config: FactorGateConfig | None = None,
        correlation_threshold: float = 0.95,
    ) -> dict[str, object]:
        created_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        formula_tokens = [int(token) for token in formula]
        formula_names = FORMULA_VOCAB.decode_tokens(formula_tokens)
        formula_hash = stable_formula_hash(
            formula_tokens=formula_tokens,
            formula_names=formula_names,
            feature_version=self.feature_version,
            operator_version=self.operator_version,
        )
        factor_id = make_factor_id(formula_hash)
        experiment_id = make_experiment_id(factor_id, created_at)

        factor_store_path = Path(factor_store_dir) if factor_store_dir is not None else Path("artifacts/factor_store")
        report_path = Path(report_dir) if report_dir is not None else Path("artifacts/reports")
        store = LocalFactorStore(factor_store_path)
        split_result = split_trade_dates(
            self.loader.trade_dates,
            train_ratio=train_ratio,
            valid_ratio=valid_ratio,
        )
        research = FactorResearchPipeline(
            evaluator=self.evaluator,
            gate_config=gate_config,
            enable_gate=enable_gate,
            correlation_threshold=correlation_threshold,
        ).run(
            factors=factors,
            raw_data=self.loader.raw_data_cache,
            target_ret=self.loader.target_ret,
            trade_dates=self.loader.trade_dates,
            ts_codes=self.loader.ts_codes,
            store=store,
            transform_method=factor_transform,
            train_ratio=train_ratio,
            valid_ratio=valid_ratio,
        )
        metrics_by_split = research.metrics_by_split
        gate_decision_payload = research.gate_decision.to_dict() if research.gate_decision is not None else None
        gate_reasons = research.gate_decision.reasons if research.gate_decision is not None else None
        factor_record = FactorRecord(
            factor_id=factor_id,
            formula=formula_names,
            formula_tokens=formula_tokens,
            formula_hash=formula_hash,
            feature_version=self.feature_version,
            operator_version=self.operator_version,
            lookback_days=self._estimate_lookback_days(formula_names),
            created_at=created_at,
            status=research.status,
            metrics=metrics_by_split["all"],
            transform_method=research.transform_method,
            gate_status=research.gate_decision.status if research.gate_decision is not None else None,
            gate_reasons=gate_reasons,
            metadata={
                "max_abs_correlation": float(research.max_abs_correlation),
                "similar_factors": research.similar_factors,
                "gate_decision": gate_decision_payload,
                "universe_name": self.universe_name,
                "universe_file": str(self.universe_file) if self.universe_file is not None else None,
            },
            factor_type="single",
        )
        experiment_record = ExperimentRecord(
            experiment_id=experiment_id,
            factor_id=factor_id,
            data_dir=str(self.data_dir),
            output_dir=str(self.output_dir),
            train_dates=split_result.train_dates,
            valid_dates=split_result.valid_dates,
            test_dates=split_result.test_dates,
            metrics_by_split=metrics_by_split,
            created_at=created_at,
        )
        factor_result = store.save_factor(factor_record)
        experiment_result = store.save_experiment(experiment_record)
        value_result = store.save_factor_values(
            factor_id,
            self.loader.ts_codes,
            self.loader.trade_dates,
            research.transformed_factors,
        )

        report = build_factor_report(
            factor_id=factor_id,
            experiment_id=experiment_id,
            formula=formula_names,
            formula_tokens=formula_tokens,
            metrics_by_split=metrics_by_split,
            n_stocks=len(self.loader.ts_codes),
            n_dates=len(self.loader.trade_dates),
            n_features=int(self.loader.feat_tensor.shape[1]),
            train_dates=split_result.train_dates,
            valid_dates=split_result.valid_dates,
            test_dates=split_result.test_dates,
            created_at=created_at,
            transform_method=research.transform_method,
            gate_decision=gate_decision_payload,
            max_abs_correlation=research.max_abs_correlation,
            similar_factors=research.similar_factors,
            status=research.status,
        )
        report_json_path, report_md_path = write_factor_report(report, report_path)

        return {
            "factor_id": factor_id,
            "experiment_id": experiment_id,
            "factor_store_dir": str(factor_store_path),
            "factor_record_path": factor_result.path,
            "experiment_record_path": experiment_result.path,
            "factor_values_path": value_result.path,
            "report_json_path": str(report_json_path),
            "report_md_path": str(report_md_path),
            "metrics_by_split": metrics_by_split,
            "transform_method": research.transform_method,
            "gate_decision": gate_decision_payload,
            "max_abs_correlation": float(research.max_abs_correlation),
            "similar_factors": research.similar_factors,
            "status": research.status,
        }

    def _summary(self, formula: list[int], metrics: FactorEvaluationResult) -> dict[str, object]:
        return {
            "data_dir": str(self.data_dir),
            "n_stocks": len(self.loader.ts_codes),
            "n_dates": len(self.loader.trade_dates),
            "n_features": int(self.loader.feat_tensor.shape[1]),
            "formula": self.vm.describe(formula),
            "formula_tokens": [int(token) for token in formula],
            "metrics": metrics.to_dict(),
            "universe_name": self.universe_name,
            "universe_file": str(self.universe_file) if self.universe_file is not None else None,
        }

    @staticmethod
    def _estimate_lookback_days(formula_names: list[str]) -> int:
        if "RET_5D" in formula_names:
            return 5
        if any(name in formula_names for name in {"TS_MEAN3", "TS_STD3", "TS_ZSCORE3"}):
            return 3
        return 1

    @staticmethod
    def _candidate_formulas() -> list[list[int]]:
        enc = FORMULA_VOCAB.encode_name
        return [
            [enc("RET_1D")],
            [enc("RET_5D")],
            [enc("TURNOVER_RATE")],
            [enc("ROE")],
            [enc("REVENUE_YOY")],
            [enc("RET_1D"), enc("CS_ZSCORE")],
            [enc("RET_1D"), enc("DELAY1")],
            [enc("RET_1D"), enc("ROE"), enc("ADD")],
            [enc("RET_5D"), enc("PB"), enc("SUB")],
            [enc("ROE"), enc("REVENUE_YOY"), enc("ADD"), enc("CS_RANK")],
        ]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the A-share factor mining engine.")
    parser.add_argument("--dry-run", action="store_true", help="Load data and evaluate a simple formula.")
    parser.add_argument("--steps", type=int, default=ModelConfig.TRAIN_STEPS)
    parser.add_argument("--batch-size", type=int, default=ModelConfig.BATCH_SIZE)
    parser.add_argument("--data-dir", default=str(ModelConfig.DATA_DIR))
    parser.add_argument("--output-dir", default=str(ModelConfig.OUTPUT_DIR))
    parser.add_argument("--factor-store-dir", default="artifacts/factor_store")
    parser.add_argument("--report-dir", default="artifacts/reports")
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--factor-transform", default="raw", choices=sorted(SUPPORTED_TRANSFORMS))
    parser.add_argument("--enable-gate", action="store_true")
    parser.add_argument("--disable-gate", action="store_true")
    parser.add_argument("--correlation-threshold", type=float, default=0.95)
    parser.add_argument("--min-coverage", type=float, default=0.8)
    parser.add_argument("--min-test-rank-ic-ir", type=float, default=-999.0)
    parser.add_argument("--min-test-score", type=float, default=-999.0)
    parser.add_argument("--max-turnover", type=float, default=1.0)
    parser.add_argument("--register", action="store_true", help="Write factor records and reports.")
    parser.add_argument("--no-register", action="store_true", help="Skip factor records and reports.")
    parser.add_argument("--train-ratio", type=float, default=0.6)
    parser.add_argument("--valid-ratio", type=float, default=0.2)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--train-mode", choices=["fixed", "neural"], default="fixed")
    parser.add_argument("--neural-warmup-steps", type=int, default=1)
    parser.add_argument("--neural-policy-steps", type=int, default=1)
    parser.add_argument("--neural-samples-per-step", type=int, default=4)
    parser.add_argument("--neural-checkpoint")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    engine = FactorMiningEngine(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        universe_file=args.universe_file,
        universe_name=args.universe_name,
    )

    register = False if args.no_register else (True if args.register else not args.dry_run)
    enable_gate = args.enable_gate and not args.disable_gate
    gate_config = FactorGateConfig(
        min_coverage=args.min_coverage,
        min_test_rank_ic_ir=args.min_test_rank_ic_ir,
        min_test_score=args.min_test_score,
        max_turnover=args.max_turnover,
        max_abs_correlation=args.correlation_threshold,
    )
    if args.dry_run:
        payload = engine.dry_run(
            register=register,
            factor_store_dir=args.factor_store_dir,
            report_dir=args.report_dir,
            train_ratio=args.train_ratio,
            valid_ratio=args.valid_ratio,
            factor_transform=args.factor_transform,
            enable_gate=enable_gate,
            gate_config=gate_config,
            correlation_threshold=args.correlation_threshold,
        )
    elif args.train_mode == "neural":
        neural_config = NeuralSearchConfig(
            max_formula_len=ModelConfig.MAX_FORMULA_LEN,
            warmup_steps=args.neural_warmup_steps,
            policy_steps=args.neural_policy_steps,
            batch_size=args.batch_size,
            samples_per_step=args.neural_samples_per_step,
            resume_checkpoint=args.neural_checkpoint,
            factor_transform=args.factor_transform,
            enable_gate=enable_gate,
            top_k=max(1, args.batch_size),
        )
        result = NeuralFormulaTrainer(
            config=neural_config,
            data_dir=args.data_dir,
            universe_name=args.universe_name,
            universe_file=args.universe_file,
            factor_store_dir=args.factor_store_dir,
            report_dir=args.report_dir,
            output_dir=args.output_dir,
            correlation_threshold=args.correlation_threshold,
            min_coverage=args.min_coverage,
        ).train()
        payload = result.to_dict() | {"train_mode": "neural"}
    else:
        payload = engine.train(
            steps=args.steps,
            batch_size=args.batch_size,
            register=register,
            factor_store_dir=args.factor_store_dir,
            report_dir=args.report_dir,
            train_ratio=args.train_ratio,
            valid_ratio=args.valid_ratio,
            factor_transform=args.factor_transform,
            enable_gate=enable_gate,
            gate_config=gate_config,
            correlation_threshold=args.correlation_threshold,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
