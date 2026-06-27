"""A-share factor mining engine."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import torch

from .backtest import AShareFactorEvaluator, FactorEvaluationResult
from .config import ModelConfig
from .data_loader import AShareDataLoader
from .vm import StackVM
from .vocab import FORMULA_VOCAB


class FactorMiningEngine:
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

    def dry_run(self) -> dict[str, object]:
        self.load_data()
        formula = [FORMULA_VOCAB.encode_name("RET_1D")]
        factors = self.vm.execute(formula, self.loader.feat_tensor)
        if factors is None:
            raise RuntimeError("failed to execute dry-run formula")
        metrics = self.evaluator.evaluate(factors, self.loader.raw_data_cache, self.loader.target_ret)
        return self._summary(formula, metrics)

    def train(self, steps: int | None = None, batch_size: int | None = None) -> dict[str, object]:
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
        return best_payload

    def _summary(self, formula: list[int], metrics: FactorEvaluationResult) -> dict[str, object]:
        return {
            "data_dir": str(self.data_dir),
            "n_stocks": len(self.loader.ts_codes),
            "n_dates": len(self.loader.trade_dates),
            "n_features": int(self.loader.feat_tensor.shape[1]),
            "formula": self.vm.describe(formula),
            "formula_tokens": [int(token) for token in formula],
            "metrics": asdict(metrics),
        }

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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    engine = FactorMiningEngine(data_dir=args.data_dir, output_dir=args.output_dir)

    payload = engine.dry_run() if args.dry_run else engine.train(steps=args.steps, batch_size=args.batch_size)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
