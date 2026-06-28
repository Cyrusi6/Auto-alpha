"""Local child-order execution simulator."""

from __future__ import annotations

from dataclasses import asdict

from execution import ExecutionFill

from backtest import AShareCostModel, AShareTradingRules

from .models import ExecutionPlanResult, ExecutionQualitySummary, ExecutionSchedule


def simulate_child_orders(
    schedule: ExecutionSchedule,
    loader,
    cost_model: AShareCostModel | None = None,
    trading_rules: AShareTradingRules | None = None,
) -> ExecutionPlanResult:
    cost_model = cost_model or AShareCostModel()
    trading_rules = trading_rules or AShareTradingRules()
    date_idx = loader.trade_dates.index(schedule.trade_date)
    close = loader.raw_data_cache["close"].detach().cpu()
    volume = loader.raw_data_cache.get("volume").detach().cpu()
    is_suspended = loader.raw_data_cache.get("is_suspended").detach().cpu()
    limit_up = loader.raw_data_cache.get("limit_up_flag").detach().cpu()
    limit_down = loader.raw_data_cache.get("limit_down_flag").detach().cpu()
    bucket_count = max(len(schedule.buckets), 1)
    fills: list[ExecutionFill] = []
    same_day_buys: set[str] = set()

    for child in schedule.child_orders:
        stock_idx = loader.ts_codes.index(child.ts_code)
        side = child.side.upper()
        price = float(close[stock_idx, date_idx].item())
        if side == "BUY":
            allowed, reason = trading_rules.can_buy(
                price,
                is_suspended=bool(is_suspended[stock_idx, date_idx].item() > 0.5),
                is_limit_up=bool(limit_up[stock_idx, date_idx].item() > 0.5),
            )
        else:
            allowed, reason = trading_rules.can_sell(
                price,
                is_suspended=bool(is_suspended[stock_idx, date_idx].item() > 0.5),
                is_limit_down=bool(limit_down[stock_idx, date_idx].item() > 0.5),
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
