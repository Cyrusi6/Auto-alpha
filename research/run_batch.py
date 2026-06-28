"""CLI for batch A-share factor research."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from factor_engine import SUPPORTED_TRANSFORMS

from .batch_runner import BatchFactorResearchRunner
from .candidates import default_candidates, load_candidates_json, save_candidates_json
from .composite import COMPOSITE_METHODS
from .models import BatchResearchConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run batch A-share factor research.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--factor-transform", default="raw", choices=sorted(SUPPORTED_TRANSFORMS))
    parser.add_argument("--enable-gate", action="store_true")
    parser.add_argument("--disable-gate", action="store_true")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--composite-method", default="rank_average", choices=sorted(COMPOSITE_METHODS))
    parser.add_argument("--correlation-threshold", type=float, default=0.95)
    parser.add_argument("--min-coverage", type=float, default=0.8)
    parser.add_argument("--candidates-json")
    parser.add_argument("--save-default-candidates")
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument("--disable-composite", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--train-ratio", type=float, default=0.6)
    parser.add_argument("--valid-ratio", type=float, default=0.2)
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="same_day_after_close")
    parser.add_argument("--min-listing-days", type=int, default=0)
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--run-leakage-audit", action="store_true")
    parser.add_argument("--leakage-audit-dir")
    parser.add_argument("--fail-on-leakage-blocker", action="store_true")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument(
        "--target-return-mode",
        choices=("adjusted_close", "raw_close", "corporate_action_total_return"),
        default="adjusted_close",
    )
    parser.add_argument("--corporate-action-dir")
    parser.add_argument("--corporate-action-cash-field", choices=("cash_div", "cash_div_tax"), default="cash_div")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    candidates = load_candidates_json(args.candidates_json) if args.candidates_json else default_candidates()
    if args.max_candidates is not None:
        candidates = candidates[: max(args.max_candidates, 0)]
    if args.save_default_candidates:
        save_candidates_json(default_candidates(), args.save_default_candidates)

    config = BatchResearchConfig(
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
        top_k=args.top_k,
        composite_method=args.composite_method,
        train_ratio=args.train_ratio,
        valid_ratio=args.valid_ratio,
        continue_on_error=args.continue_on_error,
        disable_composite=args.disable_composite,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        min_listing_days=args.min_listing_days,
        exclude_st=args.exclude_st,
        run_leakage_audit=args.run_leakage_audit,
        leakage_audit_dir=args.leakage_audit_dir,
        fail_on_leakage_blocker=args.fail_on_leakage_blocker,
        corporate_action_aware=args.corporate_action_aware,
        target_return_mode=args.target_return_mode,
        corporate_action_dir=args.corporate_action_dir,
        corporate_action_cash_field=args.corporate_action_cash_field,
    )
    result = BatchFactorResearchRunner(config=config, candidates=candidates).run()
    payload = result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
