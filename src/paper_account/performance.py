"""Paper account performance metrics."""

from __future__ import annotations

import math

from .models import PaperAccountState


def compute_account_performance(state: PaperAccountState) -> dict[str, float]:
    snapshots = state.snapshots
    equity = snapshots[-1].equity if snapshots else state.cash
    returns = [snapshot.daily_return for snapshot in snapshots if math.isfinite(snapshot.daily_return)]
    total_return = equity / state.initial_cash - 1.0 if state.initial_cash else 0.0
    mean_return = sum(returns) / len(returns) if returns else 0.0
    variance = sum((value - mean_return) ** 2 for value in returns) / len(returns) if returns else 0.0
    volatility = math.sqrt(max(variance, 0.0))
    sharpe = mean_return / volatility * math.sqrt(252.0) if volatility > 1e-12 else 0.0
    max_drawdown = _max_drawdown([snapshot.equity for snapshot in snapshots])
    filled = [entry for entry in state.trade_ledger if entry.status in {"FILLED", "PARTIAL"}]
    rejected = [entry for entry in state.trade_ledger if entry.status == "REJECTED"]
    buys = sum(entry.value for entry in filled if entry.side == "BUY")
    sells = sum(entry.value for entry in filled if entry.side == "SELL")
    turnover = (buys + sells) / max(equity, 1.0)
    fill_rate = len(filled) / len(state.trade_ledger) if state.trade_ledger else 0.0
    unfilled_value = sum(entry.value for entry in rejected)
    realized_cost = sum(entry.cost for entry in filled)
    hit_ratio = sum(1 for snapshot in snapshots if snapshot.daily_return > 0) / len(snapshots) if snapshots else 0.0
    exposure = snapshots[-1].exposure if snapshots else 0.0
    cash_ratio = snapshots[-1].cash_ratio if snapshots else (state.cash / equity if equity else 0.0)
    return {
        "total_return": float(total_return),
        "daily_returns": float(mean_return),
        "volatility": float(volatility),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_drawdown),
        "turnover": float(turnover),
        "hit_ratio": float(hit_ratio),
        "exposure": float(exposure),
        "cash_ratio": float(cash_ratio),
        "fill_rate": float(fill_rate),
        "unfilled_value": float(unfilled_value),
        "estimated_vs_realized_cost": float(realized_cost),
    }


def _max_drawdown(equity_values: list[float]) -> float:
    peak = -float("inf")
    worst = 0.0
    for value in equity_values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return abs(float(worst))
