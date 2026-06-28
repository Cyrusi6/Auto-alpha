"""NAV and performance helpers for settlement-aware accounts."""

from __future__ import annotations

from .models import AccountNavRecord


def build_account_nav_series(account_state, prices_by_date: dict[str, dict[str, float]] | None = None, settlement_events=None, lots=None) -> list[AccountNavRecord]:
    prices_by_date = prices_by_date or {}
    updated_at = str(getattr(account_state, "updated_at", "") or "")
    dates = sorted(prices_by_date) or [updated_at[:10].replace("-", "") or "UNKNOWN"]
    records: list[AccountNavRecord] = []
    previous_equity = float(account_state.initial_cash or account_state.cash or 0.0)
    realized = sum(float(record.get("realized_pnl", 0.0)) for record in getattr(account_state, "realized_pnl_ledger", []) or [])
    fees = sum(float(entry.cost) for entry in getattr(account_state, "trade_ledger", []) or [])
    taxes = sum(float(getattr(entry, "stamp_duty", 0.0)) for entry in getattr(account_state, "trade_ledger", []) or [])
    corporate_cash = sum(float(entry.cash_amount) for entry in getattr(account_state, "corporate_action_ledger", []) or [])
    for date in dates:
        prices = prices_by_date.get(date, {})
        positions_value = 0.0
        unrealized = 0.0
        for ts_code, position in account_state.positions.items():
            price = float(prices.get(ts_code, position.market_price or position.avg_cost))
            value = position.shares * price
            positions_value += value
            unrealized += value - position.avg_cost * position.shares
        unsettled_cash = float(getattr(account_state, "unsettled_receivable", 0.0) or 0.0) - float(getattr(account_state, "unsettled_payable", 0.0) or 0.0)
        equity = float(account_state.cash) + positions_value + unsettled_cash
        daily_return = equity / previous_equity - 1.0 if previous_equity else 0.0
        records.append(
            AccountNavRecord(
                trade_date=date,
                equity=float(equity),
                cash=float(account_state.cash),
                positions_value=float(positions_value),
                unsettled_cash=float(unsettled_cash),
                frozen_cash=float(getattr(account_state, "frozen_cash", 0.0) or 0.0),
                realized_pnl=float(realized),
                unrealized_pnl=float(unrealized),
                fees=float(fees),
                taxes=float(taxes),
                corporate_action_cash=float(corporate_cash),
                daily_return=float(daily_return),
            )
        )
        previous_equity = equity
    return records


def compute_account_performance(nav_records: list[AccountNavRecord] | list[dict]) -> dict[str, float]:
    payloads = [record.to_dict() if hasattr(record, "to_dict") else dict(record) for record in nav_records]
    if not payloads:
        return {
            "total_return": 0.0,
            "daily_return": 0.0,
            "max_drawdown": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_fees": 0.0,
            "total_stamp_duty": 0.0,
            "total_transfer_fee": 0.0,
            "total_slippage": 0.0,
            "corporate_action_cash": 0.0,
            "turnover": 0.0,
            "cash_drag": 0.0,
            "unsettled_cash_ratio": 0.0,
            "frozen_cash_ratio": 0.0,
        }
    first = float(payloads[0].get("equity", 0.0) or 0.0)
    last = float(payloads[-1].get("equity", 0.0) or 0.0)
    high = first
    max_dd = 0.0
    for item in payloads:
        equity = float(item.get("equity", 0.0) or 0.0)
        high = max(high, equity)
        if high:
            max_dd = max(max_dd, high / max(equity, 1e-12) - 1.0)
    return {
        "total_return": float(last / first - 1.0) if first else 0.0,
        "daily_return": float(payloads[-1].get("daily_return", 0.0) or 0.0),
        "max_drawdown": float(max_dd),
        "realized_pnl": float(payloads[-1].get("realized_pnl", 0.0) or 0.0),
        "unrealized_pnl": float(payloads[-1].get("unrealized_pnl", 0.0) or 0.0),
        "total_fees": float(payloads[-1].get("fees", 0.0) or 0.0),
        "total_stamp_duty": float(payloads[-1].get("taxes", 0.0) or 0.0),
        "total_transfer_fee": 0.0,
        "total_slippage": 0.0,
        "corporate_action_cash": float(payloads[-1].get("corporate_action_cash", 0.0) or 0.0),
        "turnover": 0.0,
        "cash_drag": float(payloads[-1].get("cash", 0.0) / last) if last else 0.0,
        "unsettled_cash_ratio": float(abs(payloads[-1].get("unsettled_cash", 0.0) or 0.0) / last) if last else 0.0,
        "frozen_cash_ratio": float(abs(payloads[-1].get("frozen_cash", 0.0) or 0.0) / last) if last else 0.0,
    }
