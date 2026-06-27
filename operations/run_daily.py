"""CLI for daily local production runs."""

from __future__ import annotations

import argparse
import json

from .daily_runner import ProductionDailyRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local daily production workflow.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--approval-store-dir", required=True)
    parser.add_argument("--paper-account-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--orders-dir", required=True)
    parser.add_argument("--factor-id")
    parser.add_argument("--latest-production", action="store_true")
    parser.add_argument("--rebalance-date")
    parser.add_argument("--portfolio-method", choices=["equal_weight", "risk_aware"], default="equal_weight")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--max-weight", type=float, default=0.10)
    parser.add_argument("--portfolio-value", type=float, default=1_000_000.0)
    parser.add_argument("--require-approval", action="store_true")
    parser.add_argument("--approval-id")
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    runner = ProductionDailyRunner(
        data_dir=args.data_dir,
        factor_store_dir=args.factor_store_dir,
        approval_store_dir=args.approval_store_dir,
        paper_account_dir=args.paper_account_dir,
        output_dir=args.output_dir,
        orders_dir=args.orders_dir,
        factor_id=args.factor_id,
        latest_production=args.latest_production,
        rebalance_date=args.rebalance_date,
        portfolio_method=args.portfolio_method,
        index_code=args.index_code,
        top_n=args.top_n,
        max_weight=args.max_weight,
        portfolio_value=args.portfolio_value,
    )
    result = runner.run(
        require_approval=args.require_approval,
        approval_id=args.approval_id,
        execute_approved=args.execute_approved,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
