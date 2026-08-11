"""Shadow-account simulation and reporting workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ShadowRunStatus:
    planned = "planned"
    running = "running"
    success = "success"
    warning = "warning"
    failed = "failed"


class ShadowExecutionMode:
    no_broker = "no_broker"
    simulated_fills = "simulated_fills"
    compare_only = "compare_only"


@dataclass(frozen=True)
class ShadowOrder:
    shadow_order_id: str
    production_run_id: str
    trade_date: str
    ts_code: str
    side: str
    order_value: float
    target_weight: float = 0.0
    parent_order_id: str | None = None
    child_order_id: str | None = None
    bucket: str | None = None
    reason: str = "shadow"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowFill:
    shadow_fill_id: str
    shadow_order_id: str
    production_run_id: str
    trade_date: str
    ts_code: str
    side: str
    value: float
    status: str
    price: float = 0.0
    shares: int = 0
    cost: float = 0.0
    reason: str = ""
    parent_order_id: str | None = None
    child_order_id: str | None = None
    bucket: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowPosition:
    production_run_id: str
    trade_date: str
    ts_code: str
    shares: int
    market_value: float
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowAccountSnapshot:
    production_run_id: str
    trade_date: str
    cash: float
    equity: float
    position_value: float
    turnover: float
    fill_rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowDriftRecord:
    production_run_id: str
    trade_date: str
    metric: str
    value: float
    threshold: float | None = None
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowPerformanceReport:
    production_run_id: str
    trade_date: str
    metrics: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowRunReport:
    production_run_id: str
    trade_date: str
    as_of_date: str
    status: str
    execution_mode: str
    summary: dict[str, Any]
    orders: list[ShadowOrder] = field(default_factory=list)
    fills: list[ShadowFill] = field(default_factory=list)
    positions: list[ShadowPosition] = field(default_factory=list)
    snapshots: list[ShadowAccountSnapshot] = field(default_factory=list)
    drift: list[ShadowDriftRecord] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "production_run_id": self.production_run_id,
            "trade_date": self.trade_date,
            "as_of_date": self.as_of_date,
            "status": self.status,
            "execution_mode": self.execution_mode,
            "summary": dict(self.summary),
            "orders": [item.to_dict() for item in self.orders],
            "fills": [item.to_dict() for item in self.fills],
            "positions": [item.to_dict() for item in self.positions],
            "snapshots": [item.to_dict() for item in self.snapshots],
            "drift": [item.to_dict() for item in self.drift],
            "paths": dict(self.paths),
        }

import json
from pathlib import Path
from typing import Any


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def find_order_records(orders_dir: str | Path, execution_plan_dir: str | Path | None = None) -> tuple[list[dict[str, Any]], str]:
    candidates = []
    if execution_plan_dir:
        root = Path(execution_plan_dir)
        candidates.extend([root / "child_orders.jsonl", root / "parent_orders.jsonl"])
    root = Path(orders_dir)
    candidates.extend([root / "plan" / "child_orders.jsonl", root / "child_orders.jsonl", root / "orders.jsonl"])
    for path in candidates:
        rows = read_jsonl(path)
        if rows:
            return rows, str(path)
    return [], ""

import json
from pathlib import Path

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def write_shadow_report(report: ShadowRunReport, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    run_path = write_json_artifact(root / "shadow_run_report.json", payload, "shadow_run_report", "shadow_trading")
    md_path = root / "shadow_run_report.md"
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    orders_path = write_jsonl_artifact(root / "shadow_orders.jsonl", [item.to_dict() for item in report.orders], "shadow_orders", "shadow_trading")
    fills_path = write_jsonl_artifact(root / "shadow_fills.jsonl", [item.to_dict() for item in report.fills], "shadow_fills", "shadow_trading")
    positions_path = write_jsonl_artifact(root / "shadow_positions.jsonl", [item.to_dict() for item in report.positions], "shadow_positions", "shadow_trading")
    snapshots_path = write_jsonl_artifact(root / "shadow_account_snapshots.jsonl", [item.to_dict() for item in report.snapshots], "shadow_account_snapshots", "shadow_trading")
    drift_path = write_json_artifact(root / "shadow_drift_report.json", {"production_run_id": report.production_run_id, "trade_date": report.trade_date, "drift": [item.to_dict() for item in report.drift], "summary": report.summary}, "shadow_drift_report", "shadow_trading")
    performance = ShadowPerformanceReport(report.production_run_id, report.trade_date, {key: float(value) for key, value in report.summary.items() if isinstance(value, (int, float))})
    performance_path = write_json_artifact(root / "shadow_performance_report.json", performance.to_dict(), "shadow_performance_report", "shadow_trading")
    comparison_path = write_json_artifact(root / "shadow_vs_production_comparison.json", {"production_run_id": report.production_run_id, "summary": report.summary}, "shadow_vs_production_comparison", "shadow_trading")
    return {
        "shadow_run_report_path": str(run_path),
        "shadow_run_report_md_path": str(md_path),
        "shadow_orders_path": str(orders_path),
        "shadow_fills_path": str(fills_path),
        "shadow_positions_path": str(positions_path),
        "shadow_account_snapshots_path": str(snapshots_path),
        "shadow_drift_report_path": str(drift_path),
        "shadow_performance_report_path": str(performance_path),
        "shadow_vs_production_comparison_path": str(comparison_path),
    }


def _render_markdown(payload: dict) -> str:
    return "\n".join(
        [
            "# Shadow Trading Run",
            "",
            f"- production_run_id: `{payload.get('production_run_id')}`",
            f"- trade_date: `{payload.get('trade_date')}`",
            f"- status: `{payload.get('status')}`",
            f"- execution_mode: `{payload.get('execution_mode')}`",
            "",
            "## Summary",
            "",
            "```json",
            json.dumps(payload.get("summary", {}), ensure_ascii=False, indent=2),
            "```",
        ]
    ) + "\n"

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any



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

import argparse
import json



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

__all__ = [
    "ShadowAccountSnapshot",
    "ShadowDriftRecord",
    "ShadowExecutionMode",
    "ShadowFill",
    "ShadowOrder",
    "ShadowPerformanceReport",
    "ShadowPosition",
    "ShadowRunReport",
    "ShadowRunStatus",
    "run_shadow_trading",
    "write_shadow_report",
]
