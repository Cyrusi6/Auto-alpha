"""CLI for shadow trading simulation."""

from __future__ import annotations

import argparse
import json

from .models import ShadowExecutionMode
from .simulator import run_shadow_trading


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local shadow trading book.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["run", "compare", "report", "smoke"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--production-run-id", required=True)
        cmd.add_argument("--data-dir")
        cmd.add_argument("--factor-store-dir")
        cmd.add_argument("--orders-dir", required=True)
        cmd.add_argument("--execution-plan-dir")
        cmd.add_argument("--paper-account-dir")
        cmd.add_argument("--portfolio-policy-path")
        cmd.add_argument("--portfolio-lab-report-path")
        cmd.add_argument("--backtest-result-path")
        cmd.add_argument("--output-dir", required=True)
        cmd.add_argument("--trade-date", required=True)
        cmd.add_argument("--as-of-date")
        cmd.add_argument("--execution-mode", choices=[ShadowExecutionMode.no_broker, ShadowExecutionMode.simulated_fills, ShadowExecutionMode.compare_only], default=ShadowExecutionMode.simulated_fills)
        cmd.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = run_shadow_trading(
        production_run_id=args.production_run_id,
        trade_date=args.trade_date,
        as_of_date=args.as_of_date or args.trade_date,
        orders_dir=args.orders_dir,
        execution_plan_dir=args.execution_plan_dir,
        output_dir=args.output_dir,
        execution_mode=args.execution_mode,
        portfolio_policy_path=args.portfolio_policy_path,
        backtest_result_path=args.backtest_result_path,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if report.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
