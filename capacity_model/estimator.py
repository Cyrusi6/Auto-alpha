"""Capacity estimation from governed A-share matrices."""

from __future__ import annotations

from typing import Sequence

import torch

from .impact import estimate_impact_cost
from .models import CapacityConfig, PortfolioCapacity, SecurityCapacity


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
