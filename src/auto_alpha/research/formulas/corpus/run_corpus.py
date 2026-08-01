"""CLI for building reusable formula corpora."""

from __future__ import annotations

import argparse
import json

from .builder import build_formula_corpus
from .models import FormulaCorpusConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a local AlphaGPT formula corpus.")
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--artifact-dir", action="append", default=[])
    parser.add_argument("--artifact-catalog-path", action="append", default=[])
    parser.add_argument("--no-defaults", action="store_true")
    parser.add_argument("--no-seed", action="store_true")
    parser.add_argument("--no-factor-store", action="store_true")
    parser.add_argument("--include-defaults", action="store_true", help="Compatibility no-op; defaults are included unless --no-defaults is set.")
    parser.add_argument("--include-seed-formulas", action="store_true", help="Compatibility no-op; seed formulas are included unless --no-seed is set.")
    parser.add_argument("--max-records", type=int)
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--valid-ratio", type=float, default=0.1)
    parser.add_argument("--preference-min-score-gap", type=float, default=0.0)
    parser.add_argument("--max-preference-pairs", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = build_formula_corpus(
        FormulaCorpusConfig(
            factor_store_dir=args.factor_store_dir,
            output_dir=args.output_dir,
            artifact_dirs=list(args.artifact_dir or []),
            artifact_catalog_paths=list(args.artifact_catalog_path or []),
            include_defaults=not args.no_defaults,
            include_seed=not args.no_seed,
            include_factor_store=not args.no_factor_store,
            max_records=args.max_records,
            train_ratio=args.train_ratio,
            valid_ratio=args.valid_ratio,
            preference_min_score_gap=args.preference_min_score_gap,
            max_preference_pairs=args.max_preference_pairs,
            seed=args.seed,
        )
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
