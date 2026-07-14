"""CLI for chunked formula batch evaluation."""

from __future__ import annotations

import argparse
import json

from factor_engine import SUPPORTED_TRANSFORMS
from research.candidates import default_candidates, load_candidates_json

from .evaluator import (
    FormulaBatchEvaluator,
    requests_from_candidates,
    requests_from_corpus,
    requests_from_requests_json,
    requests_from_requests_jsonl,
)
from .merge import merge_shard_outputs
from .models import FormulaBatchEvalConfig
from .sharding import select_shard_requests, write_shard_manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate formulas in chunks with optional matrix/cache acceleration.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--requests-json")
    parser.add_argument("--requests-jsonl")
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
    parser.add_argument("--shard-id", type=int)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-manifest-path")
    parser.add_argument("--write-shard-manifest", action="store_true")
    parser.add_argument("--merge-shards", action="store_true")
    parser.add_argument("--shard-dir", action="append", default=[])
    parser.add_argument("--merge-output-dir")
    parser.add_argument("--gpu-index", type=int)
    parser.add_argument("--device-lock-id")
    parser.add_argument("--resource-report-path")
    parser.add_argument("--feature-set-name", default="ashare_features_v1")
    parser.add_argument("--feature-set-manifest-path")
    parser.add_argument("--alpha-campaign-id")
    parser.add_argument("--feature-promotion-policy-hash")
    parser.add_argument("--research-end-date")
    parser.add_argument("--holdout-start-date")
    parser.add_argument("--label-horizon", type=int, default=1)
    parser.add_argument("--eligible-date-hash")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.merge_shards:
        payload = merge_shard_outputs(args.shard_dir, args.merge_output_dir or args.output_dir)
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0 if payload.get("status") in {"success", "warning"} else 1
    try:
        requests, source_path = _load_requests(args, parser)
    except ValueError as exc:
        parser.error(str(exc))
    requests = select_shard_requests(requests, args.shard_id, args.shard_count)
    if args.write_shard_manifest and args.shard_id is not None:
        write_shard_manifest(
            requests,
            args.output_dir,
            args.shard_id,
            args.shard_count,
            source_path,
            manifest_path=args.shard_manifest_path,
        )
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
        shard_id=args.shard_id,
        shard_count=args.shard_count,
        resource_report_path=args.resource_report_path,
        feature_set_name=args.feature_set_name,
        feature_set_manifest_path=args.feature_set_manifest_path,
        alpha_campaign_id=args.alpha_campaign_id,
        feature_promotion_policy_hash=args.feature_promotion_policy_hash,
        research_end_date=args.research_end_date,
        holdout_start_date=args.holdout_start_date,
        label_horizon=args.label_horizon,
        eligible_date_hash=args.eligible_date_hash,
    )
    result = FormulaBatchEvaluator(config).run(requests)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _load_requests(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[list, str | None]:
    sources = [
        ("--requests-json", args.requests_json),
        ("--requests-jsonl", args.requests_jsonl),
        ("--corpus-path", args.corpus_path),
        ("--candidates-json", args.candidates_json),
    ]
    provided = [(name, value) for name, value in sources if value]
    if len(provided) > 1:
        parser.error("formula request source arguments are mutually exclusive: " + ", ".join(name for name, _ in provided))
    if args.requests_json:
        requests = requests_from_requests_json(args.requests_json)
        source_path = args.requests_json
    elif args.requests_jsonl:
        requests = requests_from_requests_jsonl(args.requests_jsonl)
        source_path = args.requests_jsonl
    elif args.corpus_path:
        requests = requests_from_corpus(args.corpus_path, max_records=args.max_formulas)
        source_path = args.corpus_path
    elif args.candidates_json:
        requests = requests_from_candidates(load_candidates_json(args.candidates_json))
        source_path = args.candidates_json
        if args.max_formulas is not None:
            requests = requests[: args.max_formulas]
    else:
        requests = requests_from_candidates(default_candidates())
        source_path = None
        if args.max_formulas is not None:
            requests = requests[: args.max_formulas]
    return requests, source_path


if __name__ == "__main__":
    raise SystemExit(main())
