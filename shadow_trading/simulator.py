"""Local shadow trading simulator."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

from .book import find_order_records
from .models import (
    ShadowAccountSnapshot,
    ShadowDriftRecord,
    ShadowExecutionMode,
    ShadowFill,
    ShadowOrder,
    ShadowPerformanceReport,
    ShadowPosition,
    ShadowRunReport,
    ShadowRunStatus,
)
from .report import write_shadow_report


def run_shadow_trading(
    production_run_id: str,
    trade_date: str,
    as_of_date: str,
    orders_dir: str | Path,
    output_dir: str | Path,
    execution_plan_dir: str | Path | None = None,
    execution_mode: str = ShadowExecutionMode.simulated_fills,
    portfolio_policy_path: str | Path | None = None,
    backtest_result_path: str | Path | None = None,
    portfolio_value: float = 1_000_000.0,
) -> ShadowRunReport:
    rows, source_path = find_order_records(orders_dir, execution_plan_dir)
    orders = [_shadow_order(production_run_id, trade_date, row, idx) for idx, row in enumerate(rows)]
    fills = [_shadow_fill(order, portfolio_value) for order in orders] if execution_mode == ShadowExecutionMode.simulated_fills else []
    filled_value = sum(fill.value for fill in fills if fill.status in {"FILLED", "PARTIAL"})
    requested_value = sum(order.order_value for order in orders)
    fill_rate = filled_value / requested_value if requested_value > 1e-12 else 0.0
    positions = _positions_from_fills(production_run_id, trade_date, fills, portfolio_value)
    snapshot = ShadowAccountSnapshot(
        production_run_id=production_run_id,
        trade_date=trade_date,
        cash=max(portfolio_value - filled_value, 0.0),
        equity=portfolio_value,
        position_value=filled_value,
        turnover=requested_value / portfolio_value if portfolio_value > 0 else 0.0,
        fill_rate=fill_rate,
    )
    target_drift = 0.0 if orders else 0.0
    drift = [
        ShadowDriftRecord(production_run_id, trade_date, "target_weight_drift", float(target_drift), 0.05, "ok"),
        ShadowDriftRecord(production_run_id, trade_date, "position_weight_drift", float(1.0 - fill_rate if orders else 0.0), 0.20, "warning" if orders and fill_rate < 0.8 else "ok"),
    ]
    summary = {
        "shadow_order_count": len(orders),
        "shadow_fill_count": len(fills),
        "shadow_turnover": snapshot.turnover,
        "shadow_estimated_cost": sum(fill.cost for fill in fills),
        "shadow_capacity_warning_count": sum(1 for fill in fills if fill.status != "FILLED"),
        "shadow_risk_breach_count": 0,
        "target_weight_drift": target_drift,
        "position_weight_drift": drift[1].value,
        "expected_vs_shadow_return": 0.0,
        "shadow_fill_rate": fill_rate,
        "unfilled_shadow_value": max(requested_value - filled_value, 0.0),
        "shadow_nav": snapshot.equity,
        "orders_source_path": source_path,
        "portfolio_policy_path": str(portfolio_policy_path) if portfolio_policy_path else "",
        "backtest_result_path": str(backtest_result_path) if backtest_result_path else "",
    }
    report = ShadowRunReport(
        production_run_id=production_run_id,
        trade_date=trade_date,
        as_of_date=as_of_date,
        status=ShadowRunStatus.success,
        execution_mode=execution_mode,
        summary=summary,
        orders=orders,
        fills=fills,
        positions=positions,
        snapshots=[snapshot],
        drift=drift,
    )
    paths = write_shadow_report(report, output_dir)
    return replace(report, paths=paths)


def _shadow_order(production_run_id: str, trade_date: str, row: dict[str, Any], idx: int) -> ShadowOrder:
    child_id = row.get("child_order_id")
    parent_id = row.get("parent_order_id")
    ts_code = str(row.get("ts_code") or "")
    side = str(row.get("side") or "BUY").upper()
    key = str(child_id or row.get("order_id") or f"{production_run_id}_{idx}_{ts_code}_{side}")
    shadow_order_id = f"shadow_order_{hashlib.sha256(key.encode()).hexdigest()[:16]}"
    return ShadowOrder(
        shadow_order_id=shadow_order_id,
        production_run_id=production_run_id,
        trade_date=str(row.get("trade_date") or trade_date),
        ts_code=ts_code,
        side=side,
        order_value=float(row.get("order_value", 0.0) or 0.0),
        target_weight=float(row.get("target_weight", 0.0) or 0.0),
        parent_order_id=parent_id,
        child_order_id=child_id,
        bucket=row.get("bucket"),
        reason=str(row.get("reason") or "shadow"),
    )


def _shadow_fill(order: ShadowOrder, portfolio_value: float) -> ShadowFill:
    filled = max(float(order.order_value), 0.0)
    status = "FILLED" if filled > 0 else "REJECTED"
    price = 1.0
    shares = int(filled / price)
    fill_id = f"shadow_fill_{hashlib.sha256((order.shadow_order_id + status).encode()).hexdigest()[:16]}"
    return ShadowFill(
        shadow_fill_id=fill_id,
        shadow_order_id=order.shadow_order_id,
        production_run_id=order.production_run_id,
        trade_date=order.trade_date,
        ts_code=order.ts_code,
        side=order.side,
        value=filled if status == "FILLED" else 0.0,
        status=status,
        price=price,
        shares=shares,
        cost=filled * 0.0003 if status == "FILLED" else 0.0,
        reason="" if status == "FILLED" else "zero_order_value",
        parent_order_id=order.parent_order_id,
        child_order_id=order.child_order_id,
        bucket=order.bucket,
    )


def _positions_from_fills(production_run_id: str, trade_date: str, fills: list[ShadowFill], portfolio_value: float) -> list[ShadowPosition]:
    values: dict[str, float] = {}
    shares: dict[str, int] = {}
    for fill in fills:
        if fill.status not in {"FILLED", "PARTIAL"}:
            continue
        sign = 1 if fill.side.upper() == "BUY" else -1
        values[fill.ts_code] = values.get(fill.ts_code, 0.0) + sign * fill.value
        shares[fill.ts_code] = shares.get(fill.ts_code, 0) + sign * fill.shares
    return [
        ShadowPosition(production_run_id, trade_date, code, shares.get(code, 0), value, value / portfolio_value if portfolio_value else 0.0)
        for code, value in sorted(values.items())
    ]
