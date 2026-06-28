"""CLI for offline AlphaGPT pretraining."""

from __future__ import annotations

import argparse
import json

from .models import AlphaGPTPretrainConfig
from .pretrain import AlphaGPTPretrainer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run offline supervised AlphaGPT pretraining from formula corpus sequences.")
    parser.add_argument("--sequence-path", "--sequences-jsonl", dest="sequence_path", required=True)
    parser.add_argument("--preference-path", "--preferences-jsonl", dest="preference_path")
    parser.add_argument("--corpus-stats-path", help="Compatibility no-op; corpus stats are already embedded in pretrain output.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--max-sequences", type=int)
    parser.add_argument("--preference-steps", "--preference-epochs", dest="preference_steps", type=int, default=0)
    parser.add_argument("--preference-margin", type=float, default=0.1)
    parser.add_argument("--checkpoint-every", "--checkpoint-every-epochs", dest="checkpoint_every", type=int, default=1)
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--amp", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = AlphaGPTPretrainer(
        AlphaGPTPretrainConfig(
            sequence_path=args.sequence_path,
            preference_path=args.preference_path,
            output_dir=args.output_dir,
            seed=args.seed,
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            max_sequences=args.max_sequences,
            preference_steps=args.preference_steps,
            preference_margin=args.preference_margin,
            checkpoint_every=args.checkpoint_every,
            resume_checkpoint=args.resume_checkpoint,
            device=args.device,
            amp=args.amp,
        )
    ).train()
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
