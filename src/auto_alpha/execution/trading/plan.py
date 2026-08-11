"""Execution-plan models, simulation, scheduling, reporting, and command workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParentOrder:
    parent_order_id: str
    trade_date: str
    ts_code: str
    side: str
    target_weight: float
    order_value: float
    reason: str = "rebalance"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChildOrder:
    child_order_id: str
    parent_order_id: str
    trade_date: str
    ts_code: str
    side: str
    bucket: str
    order_value: float
    target_weight: float = 0.0
    reason: str = "rebalance"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionSchedule:
    trade_date: str
    parent_orders: list[ParentOrder]
    child_orders: list[ChildOrder]
    buckets: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "parent_orders": [order.to_dict() for order in self.parent_orders],
            "child_orders": [order.to_dict() for order in self.child_orders],
            "buckets": list(self.buckets),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ExecutionQualitySummary:
    parent_order_count: int
    child_order_count: int
    filled_child_orders: int
    partial_child_orders: int
    rejected_child_orders: int
    requested_value: float
    filled_value: float
    unfilled_order_value: float
    estimated_impact_cost: float
    realized_execution_cost: float
    execution_fill_rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionPlanResult:
    schedule: ExecutionSchedule
    fills: list[object]
    quality: ExecutionQualitySummary
    capacity_report: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule": self.schedule.to_dict(),
            "fills": [_plan_models_payload(fill) for fill in self.fills],
            "quality": self.quality.to_dict(),
            "capacity_report": self.capacity_report,
        }


def _plan_models_payload(fill: object) -> dict[str, Any]:
    if hasattr(fill, "__dataclass_fields__"):
        return {field: getattr(fill, field) for field in fill.__dataclass_fields__}
    return dict(fill)

from dataclasses import asdict

from auto_alpha.execution.trading.engine import ExecutionFill
from auto_alpha.portfolio.simulator.backtest import AShareCostModel
from auto_alpha.portfolio.simulator.backtest import AShareTradingRules



def simulate_child_orders(
    schedule: ExecutionSchedule,
    loader,
    cost_model: AShareCostModel | None = None,
    trading_rules: AShareTradingRules | None = None,
) -> ExecutionPlanResult:
    cost_model = cost_model or AShareCostModel()
    trading_rules = trading_rules or AShareTradingRules()
    date_idx = loader.trade_dates.index(schedule.trade_date)
    price_field = str(schedule.metadata.get("price_field") or "")
    if price_field != "open" or schedule.buckets != ["open"]:
        raise ValueError("daily execution simulator only supports real open fills")
    prices = loader.raw_data_cache[price_field].detach().cpu()
    volume = loader.raw_data_cache.get("volume").detach().cpu()
    is_suspended = loader.raw_data_cache.get("is_suspended").detach().cpu()
    up_limit = loader.raw_data_cache.get("up_limit")
    down_limit = loader.raw_data_cache.get("down_limit")
    open_at_up_limit = loader.raw_data_cache.get("open_at_up_limit")
    open_at_down_limit = loader.raw_data_cache.get("open_at_down_limit")
    bucket_count = max(len(schedule.buckets), 1)
    fills: list[ExecutionFill] = []
    same_day_buys: set[str] = set()

    for child in schedule.child_orders:
        stock_idx = loader.ts_codes.index(child.ts_code)
        side = child.side.upper()
        price = float(prices[stock_idx, date_idx].item())
        at_up_limit = (
            bool(open_at_up_limit.detach().cpu()[stock_idx, date_idx].item() > 0.5)
            if open_at_up_limit is not None
            else trading_rules.is_open_at_limit(
                price,
                float(up_limit.detach().cpu()[stock_idx, date_idx].item()) if up_limit is not None else 0.0,
                direction="up",
            )
        )
        at_down_limit = (
            bool(open_at_down_limit.detach().cpu()[stock_idx, date_idx].item() > 0.5)
            if open_at_down_limit is not None
            else trading_rules.is_open_at_limit(
                price,
                float(down_limit.detach().cpu()[stock_idx, date_idx].item()) if down_limit is not None else 0.0,
                direction="down",
            )
        )
        if side == "BUY":
            allowed, reason = trading_rules.can_buy(
                price,
                is_suspended=bool(is_suspended[stock_idx, date_idx].item() > 0.5),
                is_limit_up=at_up_limit,
            )
        else:
            allowed, reason = trading_rules.can_sell(
                price,
                is_suspended=bool(is_suspended[stock_idx, date_idx].item() > 0.5),
                is_limit_down=at_down_limit,
            )
            if allowed and child.ts_code in same_day_buys:
                allowed, reason = False, "t_plus_one"
        if not allowed or price <= 0:
            fills.append(_fill(child, price, 0, 0.0, 0.0, "REJECTED", reason or "invalid_price"))
            continue
        requested_shares = trading_rules.round_shares(float(child.order_value) / price)
        bucket_volume = float(volume[stock_idx, date_idx].item()) / bucket_count
        shares, volume_reason = trading_rules.volume_limited_shares(requested_shares, bucket_volume)
        if requested_shares <= 0 or shares <= 0:
            fills.append(_fill(child, price, 0, 0.0, 0.0, "REJECTED", volume_reason or "zero_shares"))
            continue
        status = "PARTIAL" if shares < requested_shares else "FILLED"
        value = float(shares * price)
        breakdown = cost_model.estimate(side, value)
        cost = float(breakdown.total)
        fills.append(_fill(child, price, int(shares), value, cost, status, volume_reason if status == "PARTIAL" else "", asdict(breakdown)))
        if side == "BUY":
            same_day_buys.add(child.ts_code)

    requested_value = sum(float(order.order_value) for order in schedule.child_orders)
    filled_value = sum(float(fill.value) for fill in fills if fill.status in {"FILLED", "PARTIAL"})
    realized_cost = sum(float(fill.cost) for fill in fills)
    estimated_impact_cost = float((schedule.metadata or {}).get("estimated_impact_cost", 0.0) or 0.0)
    quality = ExecutionQualitySummary(
        parent_order_count=len(schedule.parent_orders),
        child_order_count=len(schedule.child_orders),
        filled_child_orders=sum(1 for fill in fills if fill.status == "FILLED"),
        partial_child_orders=sum(1 for fill in fills if fill.status == "PARTIAL"),
        rejected_child_orders=sum(1 for fill in fills if fill.status == "REJECTED"),
        requested_value=float(sum(float(order.order_value) for order in schedule.parent_orders)),
        filled_value=float(filled_value),
        unfilled_order_value=float(max(sum(float(order.order_value) for order in schedule.parent_orders) - filled_value, 0.0)),
        estimated_impact_cost=float(estimated_impact_cost),
        realized_execution_cost=float(realized_cost),
        execution_fill_rate=float(filled_value / requested_value) if requested_value > 1e-12 else 0.0,
    )
    return ExecutionPlanResult(schedule=schedule, fills=fills, quality=quality)


def _fill(child, price: float, shares: int, value: float, cost: float, status: str, reason: str, cost_breakdown: dict[str, float] | None = None) -> ExecutionFill:
    cost_breakdown = cost_breakdown or {}
    return ExecutionFill(
        trade_date=child.trade_date,
        ts_code=child.ts_code,
        side=child.side.upper(),
        price=float(price),
        shares=int(shares),
        value=float(value),
        status=status,
        cost=float(cost),
        reason=reason,
        parent_order_id=child.parent_order_id,
        child_order_id=child.child_order_id,
        bucket=child.bucket,
        commission=float(cost_breakdown.get("commission", 0.0) or 0.0),
        stamp_duty=float(cost_breakdown.get("stamp_duty", 0.0) or 0.0),
        transfer_fee=float(cost_breakdown.get("transfer_fee", 0.0) or 0.0),
        slippage=float(cost_breakdown.get("slippage", 0.0) or 0.0),
        market_impact=float(cost_breakdown.get("market_impact", 0.0) or 0.0),
        other_fee=float(cost_breakdown.get("other_fee", 0.0) or 0.0),
        cost_breakdown=cost_breakdown,
    )

from dataclasses import dataclass
from typing import Sequence

from auto_alpha.portfolio.simulator.capacity import CapacityConfig, estimate_portfolio_capacity



DEFAULT_BUCKETS = ("open",)


@dataclass(frozen=True)
class ExecutionPlanConfig:
    buckets: tuple[str, ...] = DEFAULT_BUCKETS
    max_child_participation: float = 0.10
    min_child_order_value: float = 0.0
    lot_size: int = 100
    allow_partial: bool = True
    price_field: str = "open"
    capacity_lookback: int = 20
    impact_base_bps: float = 5.0
    impact_power: float = 0.5


def build_parent_orders_from_target_orders(target_orders: Sequence[object]) -> list[ParentOrder]:
    parents: list[ParentOrder] = []
    for idx, order in enumerate(target_orders):
        payload = _plan_scheduler_payload(order)
        trade_date = str(payload.get("trade_date"))
        ts_code = str(payload.get("ts_code"))
        side = str(payload.get("side", "BUY")).upper()
        parent_id = str(payload.get("parent_order_id") or f"parent_{trade_date}_{ts_code}_{idx:04d}")
        parents.append(
            ParentOrder(
                parent_order_id=parent_id,
                trade_date=trade_date,
                ts_code=ts_code,
                side=side,
                target_weight=float(payload.get("target_weight", 0.0) or 0.0),
                order_value=float(payload.get("order_value", 0.0) or 0.0),
                reason=str(payload.get("reason") or "rebalance"),
            )
        )
    return parents


def slice_parent_order(parent: ParentOrder, capacity, buckets: Sequence[str], config: ExecutionPlanConfig | None = None) -> list[ChildOrder]:
    config = config or ExecutionPlanConfig(buckets=tuple(buckets))
    bucket_list = tuple(buckets) or DEFAULT_BUCKETS
    max_value = max(float(capacity.max_trade_value), 0.0)
    remaining = max(float(parent.order_value), 0.0)
    if remaining <= 0:
        return []
    base_slice = remaining / len(bucket_list)
    if max_value > 0:
        base_slice = min(base_slice, max_value / len(bucket_list))
    child_orders: list[ChildOrder] = []
    for bucket_idx, bucket in enumerate(bucket_list):
        if remaining <= 1e-9:
            break
        value = min(base_slice, remaining)
        if value < config.min_child_order_value and remaining > config.min_child_order_value:
            continue
        child_orders.append(
            ChildOrder(
                child_order_id=f"child_{parent.parent_order_id}_{bucket_idx:02d}",
                parent_order_id=parent.parent_order_id,
                trade_date=parent.trade_date,
                ts_code=parent.ts_code,
                side=parent.side,
                bucket=str(bucket),
                order_value=float(value),
                target_weight=parent.target_weight,
                reason=parent.reason,
            )
        )
        remaining -= value
    return child_orders


def build_execution_schedule(
    parent_orders: Sequence[ParentOrder],
    loader,
    as_of_date: str,
    config: ExecutionPlanConfig | None = None,
) -> tuple[ExecutionSchedule, object]:
    config = config or ExecutionPlanConfig()
    if tuple(config.buckets) != ("open",) or config.price_field != "open":
        raise ValueError("daily next-open execution supports only the open bucket and open price")
    capacity_config = CapacityConfig(
        lookback=config.capacity_lookback,
        max_participation=config.max_child_participation,
        impact_base_bps=config.impact_base_bps,
        impact_power=config.impact_power,
    )
    portfolio_capacity = estimate_portfolio_capacity(loader, parent_orders, as_of_date, capacity_config)
    capacity_by_code = {record.ts_code: record for record in portfolio_capacity.records}
    child_orders: list[ChildOrder] = []
    for parent in parent_orders:
        child_orders.extend(slice_parent_order(parent, capacity_by_code[parent.ts_code], config.buckets, config))
    schedule = ExecutionSchedule(
        trade_date=as_of_date,
        parent_orders=list(parent_orders),
        child_orders=child_orders,
        buckets=list(config.buckets),
        metadata={
            "max_child_participation": config.max_child_participation,
            "min_child_order_value": config.min_child_order_value,
            "price_field": config.price_field,
            "estimated_impact_cost": portfolio_capacity.estimated_impact_cost,
            "capacity_warning_count": portfolio_capacity.capacity_warning_count,
        },
    )
    return schedule, portfolio_capacity


def _plan_scheduler_payload(order: object) -> dict[str, object]:
    if hasattr(order, "__dataclass_fields__"):
        return {field: getattr(order, field) for field in order.__dataclass_fields__}
    return dict(order)

import json
from pathlib import Path

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def write_execution_plan_report(result: ExecutionPlanResult, output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "execution_plan_path": root / "auto_alpha.execution.trading.plan.json",
        "execution_plan_md_path": root / "auto_alpha.execution.trading.plan.md",
        "parent_orders_path": root / "parent_orders.jsonl",
        "child_orders_path": root / "child_orders.jsonl",
        "child_fills_path": root / "child_fills.jsonl",
        "execution_quality_path": root / "execution_quality.json",
    }
    payload = result.to_dict()
    write_json_artifact(paths["execution_plan_path"], payload, artifact_type="execution_plan", producer="execution_plan")
    write_json_artifact(paths["execution_quality_path"], result.quality.to_dict(), artifact_type="execution_quality", producer="execution_plan")
    write_jsonl_artifact(paths["parent_orders_path"], [order.to_dict() for order in result.schedule.parent_orders], artifact_type="parent_orders", producer="execution_plan")
    write_jsonl_artifact(paths["child_orders_path"], [order.to_dict() for order in result.schedule.child_orders], artifact_type="child_orders", producer="execution_plan")
    write_jsonl_artifact(paths["child_fills_path"], [_plan_report_payload(fill) for fill in result.fills], artifact_type="child_fills", producer="execution_plan")
    paths["execution_plan_md_path"].write_text(_markdown(payload), encoding="utf-8")
    return paths


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _plan_report_payload(fill: object) -> dict[str, object]:
    if hasattr(fill, "__dataclass_fields__"):
        return {field: getattr(fill, field) for field in fill.__dataclass_fields__}
    return dict(fill)


def _markdown(payload: dict[str, object]) -> str:
    schedule = payload.get("schedule", {}) if isinstance(payload.get("schedule"), dict) else {}
    quality = payload.get("quality", {}) if isinstance(payload.get("quality"), dict) else {}
    child_orders = schedule.get("child_orders", []) if isinstance(schedule.get("child_orders"), list) else []
    lines = [
        "# Execution Plan",
        "",
        f"- trade_date: `{schedule.get('trade_date')}`",
        f"- parent_order_count: `{quality.get('parent_order_count', 0)}`",
        f"- child_order_count: `{quality.get('child_order_count', 0)}`",
        f"- execution_fill_rate: `{quality.get('execution_fill_rate', 0.0)}`",
        f"- unfilled_order_value: `{quality.get('unfilled_order_value', 0.0)}`",
        "",
        "| child_order_id | parent_order_id | bucket | ts_code | side | order_value |",
        "| --- | --- | --- | --- | --- | ---: |",
    ]
    for order in child_orders:
        if not isinstance(order, dict):
            continue
        lines.append(
            "| {child_order_id} | {parent_order_id} | {bucket} | {ts_code} | {side} | {order_value:.2f} |".format(
                child_order_id=order.get("child_order_id", ""),
                parent_order_id=order.get("parent_order_id", ""),
                bucket=order.get("bucket", ""),
                ts_code=order.get("ts_code", ""),
                side=order.get("side", ""),
                order_value=float(order.get("order_value", 0.0) or 0.0),
            )
        )
    return "\n".join(lines) + "\n"

import argparse
import json
from pathlib import Path

from auto_alpha.execution.trading.engine import ExecutionOrder
from auto_alpha.research.formulas.data_loader import AShareDataLoader



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

__all__ = [
    "ChildOrder",
    "DEFAULT_BUCKETS",
    "ExecutionPlanConfig",
    "ExecutionPlanResult",
    "ExecutionQualitySummary",
    "ExecutionSchedule",
    "ParentOrder",
    "build_execution_schedule",
    "build_parent_orders_from_target_orders",
    "simulate_child_orders",
    "slice_parent_order",
    "write_execution_plan_report",
]
