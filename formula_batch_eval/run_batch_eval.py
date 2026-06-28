"""CLI for chunked formula batch evaluation."""

from __future__ import annotations

import argparse
import json

from factor_engine import SUPPORTED_TRANSFORMS
from research.candidates import default_candidates, load_candidates_json

from .evaluator import FormulaBatchEvaluator, requests_from_candidates, requests_from_corpus
from .models import FormulaBatchEvalConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate formulas in chunks with optional matrix/cache acceleration.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--corpus-path", "--formula-corpus-path", dest="corpus_path")
    parser.add_argument("--candidates-json")
    parser.add_argument("--max-formulas", type=int)
    parser.add_argument("--top-k", type=int, help="Compatibility no-op; formula batch evaluation keeps all requested formulas.")
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--use-matrix-cache", action="store_true")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--strict-device", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=32)
    parser.add_argument("--factor-transform", default="raw", choices=sorted(SUPPORTED_TRANSFORMS))
    parser.add_argument("--enable-gate", action="store_true")
    parser.add_argument("--disable-gate", action="store_true")
    parser.add_argument("--correlation-threshold", type=float, default=0.95)
    parser.add_argument("--min-coverage", type=float, default=0.8)
    parser.add_argument("--train-ratio", type=float, default=0.6)
    parser.add_argument("--valid-ratio", type=float, default=0.2)
    parser.add_argument("--use-eval-cache", action="store_true")
    parser.add_argument("--eval-cache-dir")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--no-skip-existing", action="store_true")
    parser.add_argument("--register-approved", action="store_true")
    parser.add_argument("--batch-id")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.corpus_path:
        requests = requests_from_corpus(args.corpus_path, max_records=args.max_formulas)
    elif args.candidates_json:
        requests = requests_from_candidates(load_candidates_json(args.candidates_json))
        if args.max_formulas is not None:
            requests = requests[: args.max_formulas]
    else:
        requests = requests_from_candidates(default_candidates())
        if args.max_formulas is not None:
            requests = requests[: args.max_formulas]
    config = FormulaBatchEvalConfig(
        data_dir=args.data_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        factor_store_dir=args.factor_store_dir,
        report_dir=args.report_dir,
        output_dir=args.output_dir,
        matrix_cache_dir=args.matrix_cache_dir,
        use_matrix_cache=args.use_matrix_cache,
        device=args.device,
        strict_device=args.strict_device,
        factor_transform=args.factor_transform,
        enable_gate=args.enable_gate and not args.disable_gate,
        correlation_threshold=args.correlation_threshold,
        min_coverage=args.min_coverage,
        train_ratio=args.train_ratio,
        valid_ratio=args.valid_ratio,
        chunk_size=args.chunk_size,
        use_eval_cache=args.use_eval_cache,
        eval_cache_dir=args.eval_cache_dir,
        skip_existing=not args.no_skip_existing if args.skip_existing else not args.no_skip_existing,
        register_approved=args.register_approved,
        batch_id=args.batch_id,
        continue_on_error=args.continue_on_error,
    )
    result = FormulaBatchEvaluator(config).run(requests)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
