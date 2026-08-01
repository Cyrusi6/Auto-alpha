"""CLI for local execution plans."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from execution import ExecutionOrder
from model_core.data_loader import AShareDataLoader

from .models import ExecutionPlanResult
from .report import write_execution_plan_report
from .scheduler import ExecutionPlanConfig, build_execution_schedule, build_parent_orders_from_target_orders
from .simulator import simulate_child_orders


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and simulate a local execution plan.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--orders-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--as-of-date")
    parser.add_argument("--execution-buckets", default="open,morning,afternoon,close")
    parser.add_argument("--max-participation", type=float, default=0.10)
    parser.add_argument("--min-child-order-value", type=float, default=0.0)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    loader = AShareDataLoader(data_dir=args.data_dir, device="cpu").load_data()
    as_of_date = args.as_of_date or loader.trade_dates[-1]
    orders = _load_orders(args.orders_file)
    config = ExecutionPlanConfig(
        buckets=tuple(item.strip() for item in args.execution_buckets.split(",") if item.strip()),
        max_child_participation=args.max_participation,
        min_child_order_value=args.min_child_order_value,
    )
    parents = build_parent_orders_from_target_orders(orders)
    schedule, capacity = build_execution_schedule(parents, loader, as_of_date, config)
    simulated = simulate_child_orders(schedule, loader)
    result = ExecutionPlanResult(schedule=schedule, fills=simulated.fills, quality=simulated.quality, capacity_report=capacity.to_dict())
    paths = write_execution_plan_report(result, args.output_dir)
    payload = result.to_dict() | {"paths": {key: str(path) for key, path in paths.items()}}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _load_orders(path: str | Path) -> list[ExecutionOrder]:
    orders = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        orders.append(
            ExecutionOrder(
                trade_date=str(payload.get("trade_date")),
                ts_code=str(payload.get("ts_code")),
                side=str(payload.get("side")),
                target_weight=float(payload.get("target_weight", 0.0) or 0.0),
                order_value=float(payload.get("order_value", 0.0) or 0.0),
                reason=str(payload.get("reason") or "rebalance"),
            )
        )
    return orders


if __name__ == "__main__":
    raise SystemExit(main())
