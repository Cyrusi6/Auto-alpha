import math

import pytest

from execution import ExecutionFill
from paper_account import LocalPaperAccount, compute_account_performance


def test_paper_account_reset_apply_fills_and_performance(tmp_path):
    account = LocalPaperAccount(tmp_path)
    state = account.reset(100000.0)
    assert state.cash == 100000.0

    buy = ExecutionFill(
        trade_date="20240104",
        ts_code="000001.SZ",
        side="BUY",
        price=10.0,
        shares=100,
        value=1000.0,
        cost=1.0,
        status="FILLED",
        parent_order_id="parent_1",
        child_order_id="child_1",
        bucket="open",
    )
    rejected = ExecutionFill(
        trade_date="20240104",
        ts_code="600000.SH",
        side="BUY",
        price=10.0,
        shares=0,
        value=0.0,
        cost=0.0,
        status="REJECTED",
        reason="limit_up",
    )
    state = account.apply_fills([buy, rejected], {"000001.SZ": 11.0}, "20240104")
    assert state.cash == 98999.0
    assert state.positions["000001.SZ"].shares == 100
    assert "600000.SH" not in state.positions
    assert state.trade_ledger[0].child_order_id == "child_1"

    state = account.mark_to_market({"000001.SZ": 11.0}, "20240104")
    assert state.snapshots[-1].equity == 100099.0
    metrics = compute_account_performance(state)
    assert all(math.isfinite(value) for value in metrics.values())
    assert (tmp_path / "account_state.json").exists()
    assert (tmp_path / "positions.jsonl").exists()
    assert (tmp_path / "trade_ledger.jsonl").exists()

    sell = ExecutionFill(
        trade_date="20240105",
        ts_code="000001.SZ",
        side="SELL",
        price=12.0,
        shares=100,
        value=1200.0,
        cost=1.0,
        status="FILLED",
    )
    state = account.apply_fills([sell], {"000001.SZ": 12.0}, "20240105")
    assert state.cash == 100198.0
    assert "000001.SZ" not in state.positions

    with pytest.raises(ValueError):
        account.apply_fills([sell], {"000001.SZ": 12.0}, "20240105")
