"""Lagged-liquidity capacity estimation, impact, reporting, and command workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SecurityCapacity:
    ts_code: str
    trade_date: str
    side: str
    order_value: float
    order_shares: int
    avg_daily_amount: float
    avg_daily_volume: float
    volatility: float
    amount_participation: float
    volume_participation: float
    max_trade_value: float
    max_trade_shares: int
    estimated_impact_cost: float
    capacity_score: float
    capacity_warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioCapacity:
    trade_date: str
    records: list[SecurityCapacity]
    total_order_value: float
    max_amount_participation: float
    max_volume_participation: float
    estimated_impact_cost: float
    capacity_warning_count: int
    capacity_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "records": [record.to_dict() for record in self.records],
            "total_order_value": float(self.total_order_value),
            "max_amount_participation": float(self.max_amount_participation),
            "max_volume_participation": float(self.max_volume_participation),
            "estimated_impact_cost": float(self.estimated_impact_cost),
            "capacity_warning_count": int(self.capacity_warning_count),
            "capacity_score": float(self.capacity_score),
        }


@dataclass(frozen=True)
class CapacityConfig:
    lookback: int = 20
    max_participation: float = 0.10
    impact_base_bps: float = 5.0
    impact_power: float = 0.5


@dataclass(frozen=True)
class CapacityReport:
    trade_date: str
    config: CapacityConfig
    portfolio: PortfolioCapacity
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "config": asdict(self.config),
            "portfolio": self.portfolio.to_dict(),
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

import math
from dataclasses import replace



def estimate_impact_cost(
    order_value: float,
    avg_daily_amount: float,
    volatility: float,
    side: str,
    base_bps: float = 5.0,
    impact_power: float = 0.5,
) -> float:
    value = max(float(order_value), 0.0)
    amount = max(float(avg_daily_amount), 1.0)
    vol = max(float(volatility), 0.0)
    if value <= 0 or not math.isfinite(value):
        return 0.0
    participation = max(value / amount, 0.0)
    side_multiplier = 1.0 if str(side).upper() == "BUY" else 0.9
    impact_bps = float(base_bps) * (participation ** max(float(impact_power), 0.01)) * (1.0 + vol) * side_multiplier
    return float(value * impact_bps / 10000.0)


def estimate_capacity_adjusted_order(order, capacity: SecurityCapacity):
    order_value = min(float(getattr(order, "order_value", 0.0)), float(capacity.max_trade_value))
    if order_value == float(getattr(order, "order_value", 0.0)):
        return order
    if hasattr(order, "__dataclass_fields__"):
        return replace(order, order_value=float(order_value), reason=f"{getattr(order, 'reason', 'rebalance')}:capacity_adjusted")
    payload = dict(order)
    payload["order_value"] = float(order_value)
    payload["reason"] = f"{payload.get('reason', 'rebalance')}:capacity_adjusted"
    return payload

import json
from datetime import datetime
from pathlib import Path

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



def build_capacity_report(
    portfolio: PortfolioCapacity,
    config: CapacityConfig,
    metadata: dict[str, object] | None = None,
) -> CapacityReport:
    return CapacityReport(
        trade_date=portfolio.trade_date,
        config=config,
        portfolio=portfolio,
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        metadata=metadata or {},
    )


def write_capacity_report(report: CapacityReport, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "capacity_report.json"
    md_path = root / "capacity_report.md"
    payload = report.to_dict()
    write_json_artifact(json_path, payload, artifact_type="capacity_report", producer="capacity_model")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    return json_path, md_path


def _markdown(payload: dict[str, object]) -> str:
    portfolio = payload.get("portfolio", {}) if isinstance(payload.get("portfolio"), dict) else {}
    records = portfolio.get("records", []) if isinstance(portfolio.get("records"), list) else []
    lines = [
        "# Capacity Report",
        "",
        f"- trade_date: `{payload.get('trade_date')}`",
        f"- total_order_value: `{portfolio.get('total_order_value', 0.0)}`",
        f"- estimated_impact_cost: `{portfolio.get('estimated_impact_cost', 0.0)}`",
        f"- capacity_warning_count: `{portfolio.get('capacity_warning_count', 0)}`",
        "",
        "| ts_code | side | order_value | amount_participation | volume_participation | impact_cost | warning |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for record in records:
        if not isinstance(record, dict):
            continue
        lines.append(
            "| {ts_code} | {side} | {order_value:.2f} | {amount_participation:.4f} | {volume_participation:.4f} | {estimated_impact_cost:.2f} | {capacity_warning} |".format(
                ts_code=record.get("ts_code", ""),
                side=record.get("side", ""),
                order_value=float(record.get("order_value", 0.0) or 0.0),
                amount_participation=float(record.get("amount_participation", 0.0) or 0.0),
                volume_participation=float(record.get("volume_participation", 0.0) or 0.0),
                estimated_impact_cost=float(record.get("estimated_impact_cost", 0.0) or 0.0),
                capacity_warning=record.get("capacity_warning", ""),
            )
        )
    return "\n".join(lines) + "\n"

from typing import Sequence

import torch



def estimate_security_capacity(
    loader,
    ts_code: str,
    as_of_date: str,
    lookback: int = 20,
    max_participation: float = 0.10,
    order_value: float = 0.0,
    side: str = "BUY",
    impact_base_bps: float = 5.0,
    impact_power: float = 0.5,
) -> SecurityCapacity:
    if ts_code not in loader.ts_codes:
        raise ValueError(f"unknown ts_code: {ts_code}")
    if as_of_date not in loader.trade_dates:
        raise ValueError(f"as_of_date is not in loaded trade dates: {as_of_date}")
    stock_idx = loader.ts_codes.index(ts_code)
    date_idx = loader.trade_dates.index(as_of_date)
    start_idx = max(0, date_idx - max(int(lookback), 1) + 1)
    amount = _field(loader, "amount")[stock_idx, start_idx : date_idx + 1]
    volume = _field(loader, "volume")[stock_idx, start_idx : date_idx + 1]
    close = _field(loader, "close")[stock_idx, date_idx]
    returns = loader.target_ret.detach().cpu()[stock_idx, start_idx : date_idx + 1]
    avg_amount = float(torch.clamp(torch.nan_to_num(amount).mean(), min=0.0).item()) if amount.numel() else 0.0
    avg_volume = float(torch.clamp(torch.nan_to_num(volume).mean(), min=0.0).item()) if volume.numel() else 0.0
    volatility = float(torch.nan_to_num(returns.std(unbiased=False), nan=0.0).item()) if returns.numel() else 0.0
    value = max(float(order_value), 0.0)
    price = max(float(close.item()), 1e-6)
    order_shares = int(value / price) if value > 0 else 0
    max_trade_value = max(avg_amount * max(float(max_participation), 0.0), 0.0)
    max_trade_shares = int(max(avg_volume * max(float(max_participation), 0.0), 0.0))
    amount_participation = value / avg_amount if avg_amount > 1e-12 else 0.0
    volume_participation = order_shares / avg_volume if avg_volume > 1e-12 else 0.0
    impact = estimate_impact_cost(value, avg_amount, volatility, side, impact_base_bps, impact_power)
    max_ratio = max(amount_participation, volume_participation)
    warning = ""
    if max_ratio > max_participation + 1e-12:
        warning = "participation_above_limit"
    elif avg_amount <= 0 or avg_volume <= 0:
        warning = "missing_capacity_inputs"
    score = 1.0 / (1.0 + max_ratio + volatility)
    return SecurityCapacity(
        ts_code=ts_code,
        trade_date=as_of_date,
        side=str(side).upper(),
        order_value=float(value),
        order_shares=int(order_shares),
        avg_daily_amount=float(avg_amount),
        avg_daily_volume=float(avg_volume),
        volatility=float(volatility),
        amount_participation=float(amount_participation),
        volume_participation=float(volume_participation),
        max_trade_value=float(max_trade_value),
        max_trade_shares=int(max_trade_shares),
        estimated_impact_cost=float(impact),
        capacity_score=float(score),
        capacity_warning=warning,
    )


def estimate_portfolio_capacity(
    loader,
    target_orders: Sequence[object],
    as_of_date: str,
    config: CapacityConfig | None = None,
) -> PortfolioCapacity:
    config = config or CapacityConfig()
    records = [
        estimate_security_capacity(
            loader,
            ts_code=str(_payload(order).get("ts_code")),
            as_of_date=as_of_date,
            lookback=config.lookback,
            max_participation=config.max_participation,
            order_value=float(_payload(order).get("order_value", 0.0) or 0.0),
            side=str(_payload(order).get("side", "BUY")),
            impact_base_bps=config.impact_base_bps,
            impact_power=config.impact_power,
        )
        for order in target_orders
    ]
    total_order_value = sum(record.order_value for record in records)
    warning_count = sum(1 for record in records if record.capacity_warning)
    return PortfolioCapacity(
        trade_date=as_of_date,
        records=records,
        total_order_value=float(total_order_value),
        max_amount_participation=max((record.amount_participation for record in records), default=0.0),
        max_volume_participation=max((record.volume_participation for record in records), default=0.0),
        estimated_impact_cost=float(sum(record.estimated_impact_cost for record in records)),
        capacity_warning_count=int(warning_count),
        capacity_score=float(sum(record.capacity_score for record in records) / len(records)) if records else 0.0,
    )


def rank_capacity(records: Sequence[SecurityCapacity]) -> list[SecurityCapacity]:
    return sorted(records, key=lambda record: (record.capacity_warning != "", -record.capacity_score, record.ts_code))


def _field(loader, name: str) -> torch.Tensor:
    values = loader.raw_data_cache.get(name)
    if values is None:
        close = loader.raw_data_cache["close"]
        return torch.zeros_like(close).detach().cpu()
    return torch.nan_to_num(values.detach().cpu().to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)


def _payload(order: object) -> dict[str, object]:
    if hasattr(order, "__dataclass_fields__"):
        return {field: getattr(order, field) for field in order.__dataclass_fields__}
    return dict(order)

import argparse
import json
from pathlib import Path

from auto_alpha.portfolio.simulator.backtest import build_long_only_targets, factor_values_to_matrix, select_factor_id
from auto_alpha.execution.trading.engine import ExecutionOrder
from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.research.formulas.data_loader import AShareDataLoader



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

__all__ = [
    "CapacityConfig",
    "CapacityReport",
    "PortfolioCapacity",
    "SecurityCapacity",
    "build_capacity_report",
    "estimate_capacity_adjusted_order",
    "estimate_impact_cost",
    "estimate_portfolio_capacity",
    "estimate_security_capacity",
    "rank_capacity",
    "write_capacity_report",
]
