"""A-share factor mining engine."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import torch

from evaluation import build_factor_report, evaluate_by_splits, split_trade_dates, write_factor_report
from factor_store import (
    ExperimentRecord,
    FactorRecord,
    LocalFactorStore,
    make_experiment_id,
    make_factor_id,
    stable_formula_hash,
)

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
    ):
        self.data_dir = Path(data_dir) if data_dir is not None else Path(ModelConfig.DATA_DIR)
        self.output_dir = Path(output_dir) if output_dir is not None else Path(ModelConfig.OUTPUT_DIR)
        self.device = torch.device(device) if device is not None else ModelConfig.DEVICE
        self.loader = AShareDataLoader(data_dir=self.data_dir, device=self.device)
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

        split_result = split_trade_dates(
            self.loader.trade_dates,
            train_ratio=train_ratio,
            valid_ratio=valid_ratio,
        )
        metrics_by_split = evaluate_by_splits(
            self.evaluator,
            factors,
            self.loader.raw_data_cache,
            self.loader.target_ret,
            self.loader.trade_dates,
            split_result,
        )

        factor_store_path = Path(factor_store_dir) if factor_store_dir is not None else Path("artifacts/factor_store")
        report_path = Path(report_dir) if report_dir is not None else Path("artifacts/reports")
        store = LocalFactorStore(factor_store_path)
        factor_record = FactorRecord(
            factor_id=factor_id,
            formula=formula_names,
            formula_tokens=formula_tokens,
            formula_hash=formula_hash,
            feature_version=self.feature_version,
            operator_version=self.operator_version,
            lookback_days=self._estimate_lookback_days(formula_names),
            created_at=created_at,
            metrics=metrics_by_split["all"],
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
            factors,
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
    parser.add_argument("--register", action="store_true", help="Write factor records and reports.")
    parser.add_argument("--no-register", action="store_true", help="Skip factor records and reports.")
    parser.add_argument("--train-ratio", type=float, default=0.6)
    parser.add_argument("--valid-ratio", type=float, default=0.2)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    engine = FactorMiningEngine(data_dir=args.data_dir, output_dir=args.output_dir)

    register = False if args.no_register else (True if args.register else not args.dry_run)
    if args.dry_run:
        payload = engine.dry_run(
            register=register,
            factor_store_dir=args.factor_store_dir,
            report_dir=args.report_dir,
            train_ratio=args.train_ratio,
            valid_ratio=args.valid_ratio,
        )
    else:
        payload = engine.train(
            steps=args.steps,
            batch_size=args.batch_size,
            register=register,
            factor_store_dir=args.factor_store_dir,
            report_dir=args.report_dir,
            train_ratio=args.train_ratio,
            valid_ratio=args.valid_ratio,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
