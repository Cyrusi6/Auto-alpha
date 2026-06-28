import math
import json

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

    replay = account.apply_fills([sell], {"000001.SZ": 12.0}, "20240105")
    assert replay.cash == state.cash

    oversell = ExecutionFill(
        trade_date="20240105",
        ts_code="000001.SZ",
        side="SELL",
        price=12.0,
        shares=200,
        value=2400.0,
        cost=1.0,
        status="FILLED",
    )
    with pytest.raises(ValueError):
        account.apply_fills([oversell], {"000001.SZ": 12.0}, "20240105")


def test_paper_account_broker_fill_idempotency(tmp_path):
    account = LocalPaperAccount(tmp_path)
    account.reset(100000.0)
    fill = ExecutionFill(
        trade_date="20240104",
        ts_code="000001.SZ",
        side="BUY",
        price=10.0,
        shares=100,
        value=1000.0,
        status="FILLED",
        cost=1.0,
        broker_order_id="bo_1",
        broker_fill_id="bf_1",
        client_order_id="child_1",
        broker_adapter="simulated",
        broker_batch_id="batch_1",
    )
    rejected = ExecutionFill(
        trade_date="20240104",
        ts_code="000001.SZ",
        side="BUY",
        price=10.0,
        shares=0,
        value=0.0,
        status="REJECTED",
        broker_order_id="bo_2",
        broker_fill_id="bf_2",
        broker_batch_id="batch_1",
    )

    first = account.apply_child_fills([fill, rejected], {"000001.SZ": 10.0}, "20240104")
    second = account.apply_child_fills([fill, rejected], {"000001.SZ": 10.0}, "20240104")

    assert first.cash == second.cash
    assert second.positions["000001.SZ"].shares == 100
    assert len(second.trade_ledger) == 2
    assert second.trade_ledger[0].broker_fill_id == "bf_1"


def test_paper_account_loads_legacy_state_and_settlement_idempotent(tmp_path):
    data_dir = tmp_path / "data"
    cal_dir = data_dir / "trade_calendar"
    cal_dir.mkdir(parents=True)
    (cal_dir / "records.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"trade_date": "20240102", "is_open": True}),
                json.dumps({"trade_date": "20240103", "is_open": True}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    account = LocalPaperAccount(tmp_path / "account")
    account.root_dir.mkdir(parents=True)
    account.state_path.write_text(
        json.dumps({"account_id": "paper_ashare", "initial_cash": 10000.0, "cash": 10000.0, "positions": {}, "cash_ledger": [], "trade_ledger": []}),
        encoding="utf-8",
    )
    state = account.load_state()
    assert state.available_cash == 10000.0
    assert state.position_lots == []

    fill = ExecutionFill(
        trade_date="20240102",
        ts_code="000001.SZ",
        side="BUY",
        price=10.0,
        shares=100,
        value=1000.0,
        status="FILLED",
        cost=1.0,
        broker_fill_id="settlement_bf_1",
        child_order_id="settlement_child_1",
    )
    first = account.apply_fills_settlement_aware([fill], data_dir, "20240102", prices={"000001.SZ": 10.0})
    second = account.apply_fills_settlement_aware([fill], data_dir, "20240102", prices={"000001.SZ": 10.0})

    assert first.cash == second.cash
    assert len(second.trade_ledger) == 1
    assert len(second.settlement_events) == 2
    assert (account.settlement_events_path).exists()
