"""CLI for neural-guided formula search."""

from __future__ import annotations

import argparse
import json

from factor_engine import SUPPORTED_TRANSFORMS
from research.composite import COMPOSITE_METHODS

from .models import NeuralSearchConfig
from .trainer import NeuralFormulaTrainer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local neural-guided A-share formula search.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--warmup-steps", type=int, default=2)
    parser.add_argument("--policy-steps", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--samples-per-step", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--max-formula-len", type=int, default=8)
    parser.add_argument("--min-formula-len", type=int, default=2)
    parser.add_argument("--max-complexity", type=int, default=24)
    parser.add_argument("--max-lookback", type=int, default=10)
    parser.add_argument("--checkpoint-every", type=int, default=1)
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--corpus-sequence-path")
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--use-matrix-cache", action="store_true")
    parser.add_argument("--use-batch-eval", action="store_true")
    parser.add_argument("--batch-eval-output-dir")
    parser.add_argument("--batch-eval-chunk-size", type=int, default=32)
    parser.add_argument("--batch-eval-device", default="auto")
    parser.add_argument("--use-eval-cache", action="store_true")
    parser.add_argument("--eval-cache-dir")
    parser.add_argument("--factor-transform", default="raw", choices=sorted(SUPPORTED_TRANSFORMS))
    parser.add_argument("--enable-gate", action="store_true")
    parser.add_argument("--disable-gate", action="store_true")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--composite-method", default="rank_average", choices=sorted(COMPOSITE_METHODS))
    parser.add_argument("--correlation-threshold", type=float, default=0.95)
    parser.add_argument("--min-coverage", type=float, default=0.8)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = NeuralSearchConfig(
        seed=args.seed,
        max_formula_len=args.max_formula_len,
        min_formula_len=args.min_formula_len,
        warmup_steps=args.warmup_steps,
        policy_steps=args.policy_steps,
        batch_size=args.batch_size,
        samples_per_step=args.samples_per_step,
        learning_rate=args.learning_rate,
        max_complexity=args.max_complexity,
        max_lookback=args.max_lookback,
        checkpoint_every=args.checkpoint_every,
        resume_checkpoint=args.resume_checkpoint,
        device=args.device,
        factor_transform=args.factor_transform,
        enable_gate=args.enable_gate and not args.disable_gate,
        top_k=args.top_k,
        composite_method=args.composite_method,
        corpus_sequence_path=args.corpus_sequence_path,
        matrix_cache_dir=args.matrix_cache_dir,
        use_matrix_cache=args.use_matrix_cache,
        use_batch_eval=args.use_batch_eval,
        batch_eval_output_dir=args.batch_eval_output_dir,
        batch_eval_chunk_size=args.batch_eval_chunk_size,
        batch_eval_device=args.batch_eval_device,
        use_eval_cache=args.use_eval_cache,
        eval_cache_dir=args.eval_cache_dir,
    )
    result = NeuralFormulaTrainer(
        config=config,
        data_dir=args.data_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        factor_store_dir=args.factor_store_dir,
        report_dir=args.report_dir,
        output_dir=args.output_dir,
        correlation_threshold=args.correlation_threshold,
        min_coverage=args.min_coverage,
    ).train()
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
