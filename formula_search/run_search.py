"""CLI for search-style formula research."""

from __future__ import annotations

import argparse
import json

from factor_engine import SUPPORTED_TRANSFORMS
from research.composite import COMPOSITE_METHODS

from .models import FormulaSearchConfig
from .search import FormulaSearchRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local formula search for A-share factors.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--population-size", type=int, default=20)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--max-formula-len", type=int, default=8)
    parser.add_argument("--max-complexity", type=int, default=20)
    parser.add_argument("--max-lookback", type=int, default=10)
    parser.add_argument("--mutation-rate", type=float, default=0.7)
    parser.add_argument("--crossover-rate", type=float, default=0.3)
    parser.add_argument("--elite-size", type=int, default=5)
    parser.add_argument("--candidate-batch-size", type=int)
    parser.add_argument("--factor-transform", default="raw", choices=sorted(SUPPORTED_TRANSFORMS))
    parser.add_argument("--enable-gate", action="store_true")
    parser.add_argument("--disable-gate", action="store_true")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--composite-method", default="rank_average", choices=sorted(COMPOSITE_METHODS))
    parser.add_argument("--correlation-threshold", type=float, default=0.95)
    parser.add_argument("--min-coverage", type=float, default=0.8)
    parser.add_argument("--train-ratio", type=float, default=0.6)
    parser.add_argument("--valid-ratio", type=float, default=0.2)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    search_config = FormulaSearchConfig(
        seed=args.seed,
        population_size=args.population_size,
        generations=args.generations,
        max_formula_len=args.max_formula_len,
        max_complexity=args.max_complexity,
        max_lookback=args.max_lookback,
        mutation_rate=args.mutation_rate,
        crossover_rate=args.crossover_rate,
        elite_size=args.elite_size,
        top_k=args.top_k,
        candidate_batch_size=args.candidate_batch_size,
    )
    result = FormulaSearchRunner(
        search_config=search_config,
        data_dir=args.data_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        factor_store_dir=args.factor_store_dir,
        report_dir=args.report_dir,
        output_dir=args.output_dir,
        factor_transform=args.factor_transform,
        enable_gate=args.enable_gate and not args.disable_gate,
        correlation_threshold=args.correlation_threshold,
        min_coverage=args.min_coverage,
        composite_method=args.composite_method,
        train_ratio=args.train_ratio,
        valid_ratio=args.valid_ratio,
        continue_on_error=args.continue_on_error,
    ).run()
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
