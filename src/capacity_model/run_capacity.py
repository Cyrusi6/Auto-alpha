"""CLI for local capacity reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backtest import build_long_only_targets, factor_values_to_matrix, select_factor_id
from execution import ExecutionOrder
from factor_store import LocalFactorStore
from model_core.data_loader import AShareDataLoader

from .estimator import estimate_portfolio_capacity
from .models import CapacityConfig
from .report import build_capacity_report, write_capacity_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Estimate local A-share order capacity.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--orders-file")
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--factor-id")
    parser.add_argument("--latest-approved", action="store_true")
    parser.add_argument("--factor-type", choices=["single", "composite", "any"], default="any")
    parser.add_argument("--as-of-date")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--max-weight", type=float, default=0.10)
    parser.add_argument("--portfolio-value", type=float, default=1_000_000.0)
    parser.add_argument("--lookback", type=int, default=20)
    parser.add_argument("--max-participation", type=float, default=0.10)
    parser.add_argument("--impact-base-bps", type=float, default=5.0)
    parser.add_argument("--impact-power", type=float, default=0.5)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    loader = AShareDataLoader(data_dir=args.data_dir, device="cpu").load_data()
    as_of_date = args.as_of_date or loader.trade_dates[-1]
    orders = _load_orders(args.orders_file) if args.orders_file else _orders_from_factor(args, loader)
    config = CapacityConfig(
        lookback=args.lookback,
        max_participation=args.max_participation,
        impact_base_bps=args.impact_base_bps,
        impact_power=args.impact_power,
    )
    portfolio = estimate_portfolio_capacity(loader, orders, as_of_date, config)
    report = build_capacity_report(portfolio, config, {"orders": len(orders)})
    json_path, md_path = write_capacity_report(report, args.output_dir)
    payload = report.to_dict() | {
        "paths": {"capacity_report_path": str(json_path), "capacity_report_md_path": str(md_path)}
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _load_orders(path: str | None) -> list[ExecutionOrder]:
    if not path:
        return []
    records: list[ExecutionOrder] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        records.append(
            ExecutionOrder(
                trade_date=str(payload.get("trade_date")),
                ts_code=str(payload.get("ts_code")),
                side=str(payload.get("side")),
                target_weight=float(payload.get("target_weight", 0.0) or 0.0),
                order_value=float(payload.get("order_value", 0.0) or 0.0),
                reason=str(payload.get("reason") or "rebalance"),
            )
        )
    return records


def _orders_from_factor(args, loader) -> list[ExecutionOrder]:
    if not args.factor_store_dir:
        raise ValueError("either --orders-file or --factor-store-dir is required")
    store = LocalFactorStore(args.factor_store_dir)
    factor_id = select_factor_id(store, args.factor_id, latest_approved=args.latest_approved, factor_type=args.factor_type)
    records = store.load_factor_values(factor_id)
    matrix = factor_values_to_matrix(records, loader.ts_codes, loader.trade_dates)
    as_of_date = args.as_of_date or loader.trade_dates[-1]
    date_idx = loader.trade_dates.index(as_of_date)
    targets = build_long_only_targets(
        matrix[:, date_idx : date_idx + 1],
        loader.ts_codes,
        [as_of_date],
        top_n=args.top_n,
        max_weight=args.max_weight,
    )[0]
    return [
        ExecutionOrder(
            trade_date=target.trade_date,
            ts_code=target.ts_code,
            side="BUY",
            target_weight=target.target_weight,
            order_value=float(target.target_weight) * float(args.portfolio_value),
            reason="capacity_estimate",
        )
        for target in targets
    ]


if __name__ == "__main__":
    raise SystemExit(main())
