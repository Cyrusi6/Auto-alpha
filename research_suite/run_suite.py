"""CLI for one-click A-share research suites."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .models import ResearchSuiteConfig
from .workflow import ResearchSuiteRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a full local A-share research suite.")
    parser.add_argument("--config-json")
    parser.add_argument("--write-default-config")
    parser.add_argument("--suite-name", default="sample_suite")
    parser.add_argument("--provider", default="sample")
    parser.add_argument("--data-dir")
    parser.add_argument("--universe-name", default="csi300_sample")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--report-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--backtest-dir")
    parser.add_argument("--orders-dir")
    parser.add_argument("--as-of-date", default="20240104")
    parser.add_argument("--factor-transform", default="winsorize_zscore")
    parser.add_argument("--search-mode", choices=["random", "neural", "hybrid"], default="random")
    parser.add_argument("--search-seed", type=int, default=42)
    parser.add_argument("--search-population-size", type=int, default=12)
    parser.add_argument("--search-generations", type=int, default=2)
    parser.add_argument("--search-max-candidates", type=int)
    parser.add_argument("--neural-warmup-steps", type=int, default=1)
    parser.add_argument("--neural-policy-steps", type=int, default=1)
    parser.add_argument("--neural-checkpoint")
    parser.add_argument("--hybrid-neural-ratio", type=float, default=0.5)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--composite-method", default="rank_average")
    parser.add_argument("--portfolio-method", choices=["equal_weight", "risk_aware"], default="equal_weight")
    parser.add_argument("--risk-aversion", type=float, default=1.0)
    parser.add_argument("--turnover-penalty", type=float, default=0.1)
    parser.add_argument("--max-turnover", type=float, default=1.0)
    parser.add_argument("--max-industry-active-weight", type=float, default=0.20)
    parser.add_argument("--max-tracking-error", type=float, default=1.0)
    parser.add_argument("--promote-latest-composite", action="store_true")
    parser.add_argument("--skip-data-sync", action="store_true")
    parser.add_argument("--skip-universe", action="store_true")
    parser.add_argument("--skip-orders", action="store_true")
    parser.add_argument("--disable-promotion", action="store_true")
    parser.add_argument("--walk-forward-train-size", type=int, default=1)
    parser.add_argument("--walk-forward-test-size", type=int, default=1)
    parser.add_argument("--walk-forward-step-size", type=int, default=1)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.write_default_config:
        config = _default_config(args)
        path = Path(args.write_default_config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"config_path": str(path)}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0

    config = _load_config(args.config_json) if args.config_json else _default_config(args)
    result = ResearchSuiteRunner(config).run()
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.status == "success" else 1


def _load_config(path: str) -> ResearchSuiteConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ResearchSuiteConfig(**payload)


def _default_config(args: argparse.Namespace) -> ResearchSuiteConfig:
    base_dir = Path(args.output_dir).parent if args.output_dir else Path("/tmp/auto-alpha-suite")
    return ResearchSuiteConfig(
        suite_name=args.suite_name,
        data_dir=str(args.data_dir or base_dir / "data"),
        universe_name=args.universe_name,
        index_code=args.index_code,
        factor_store_dir=str(args.factor_store_dir or base_dir / "store"),
        report_dir=str(args.report_dir or base_dir / "reports"),
        output_dir=str(args.output_dir or base_dir / "suite"),
        backtest_dir=str(args.backtest_dir or base_dir / "backtest"),
        orders_dir=str(args.orders_dir or base_dir / "orders"),
        provider=args.provider,
        as_of_date=args.as_of_date,
        factor_transform=args.factor_transform,
        search_mode=args.search_mode,
        search_seed=args.search_seed,
        search_population_size=args.search_population_size,
        search_generations=args.search_generations,
        search_max_candidates=args.search_max_candidates,
        neural_warmup_steps=args.neural_warmup_steps,
        neural_policy_steps=args.neural_policy_steps,
        neural_checkpoint=args.neural_checkpoint,
        hybrid_neural_ratio=args.hybrid_neural_ratio,
        top_k=args.top_k,
        composite_method=args.composite_method,
        portfolio_method=args.portfolio_method,
        risk_aversion=args.risk_aversion,
        turnover_penalty=args.turnover_penalty,
        max_turnover=args.max_turnover,
        max_industry_active_weight=args.max_industry_active_weight,
        max_tracking_error=args.max_tracking_error,
        promote_latest_composite=args.promote_latest_composite,
        pretty=args.pretty,
        skip_data_sync=args.skip_data_sync,
        skip_universe=args.skip_universe,
        skip_orders=args.skip_orders,
        disable_promotion=args.disable_promotion,
        walk_forward_train_size=args.walk_forward_train_size,
        walk_forward_test_size=args.walk_forward_test_size,
        walk_forward_step_size=args.walk_forward_step_size,
    )


if __name__ == "__main__":
    raise SystemExit(main())
