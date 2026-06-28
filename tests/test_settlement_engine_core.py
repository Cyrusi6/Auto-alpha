import json
import math

from artifact_schema.validator import validate_artifact
from execution import ExecutionFill, ExecutionOrder
from paper_account import LocalPaperAccount
from settlement_engine import (
    SettlementCalendar,
    SettlementEventType,
    allocate_sell_lots,
    build_settlement_events_from_fills,
    estimate_fee_tax,
    load_settlement_profile,
    normalize_fee_tax_from_fill,
    precheck_orders_against_availability,
    settle_pending_events,
    write_fee_tax_report,
    write_settlement_report,
)
from settlement_engine.lots import apply_buy_fill_to_lots


def _write_calendar(data_dir):
    path = data_dir / "trade_calendar"
    path.mkdir(parents=True)
    rows = [
        {"trade_date": "20240102", "is_open": True},
        {"trade_date": "20240103", "is_open": True},
        {"trade_date": "20240104", "is_open": True},
    ]
    (path / "records.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_calendar_fee_tax_and_legacy_cost(tmp_path):
    data_dir = tmp_path / "data"
    _write_calendar(data_dir)
    calendar = SettlementCalendar.from_data_dir(data_dir)

    assert calendar.next_trade_date("20240102", 1) == "20240103"
    assert calendar.next_trade_date("20240102", 2) == "20240104"

    buy_fee = estimate_fee_tax("BUY", 1000.0)
    sell_fee = estimate_fee_tax("SELL", 1000.0)
    assert buy_fee.stamp_duty == 0.0
    assert sell_fee.stamp_duty > 0.0

    legacy, warnings = normalize_fee_tax_from_fill({"cost": 7.5})
    assert legacy.total == 7.5
    assert legacy.other_fee == 7.5
    assert "legacy_cost_only" in warnings

    report_path = write_fee_tax_report([{"cost": 7.5}], tmp_path / "fee_tax_report.json")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["legacy_cost_only_count"] == 1
    assert validate_artifact(report_path, strict=True).valid is True


def test_empty_account_settlement_report_is_safe(tmp_path):
    account = LocalPaperAccount(tmp_path / "account")
    state = account.load_state()

    paths = write_settlement_report(state, tmp_path / "settlement", "20240104")

    payload = json.loads(open(paths["settlement_report_path"], encoding="utf-8").read())
    assert payload["account_id"] == "paper_ashare"
    assert payload["pending_settlement_event_count"] == 0
    assert validate_artifact(paths["settlement_report_path"], strict=True).valid is True


def test_settlement_events_lots_pnl_and_idempotency(tmp_path):
    data_dir = tmp_path / "data"
    _write_calendar(data_dir)
    profile = load_settlement_profile("cn_ashare_paper_default", cost_basis_method="fifo")
    account = LocalPaperAccount(tmp_path / "account")
    state = account.reset(100000.0)

    buy = ExecutionFill(
        trade_date="20240102",
        ts_code="000001.SZ",
        side="BUY",
        price=10.0,
        shares=100,
        value=1000.0,
        status="FILLED",
        cost=1.0,
        broker_fill_id="bf_buy",
        child_order_id="child_buy",
    )
    events = build_settlement_events_from_fills([buy], "20240102", profile, SettlementCalendar.from_data_dir(data_dir))

    assert {event.event_type for event in events} == {SettlementEventType.trade_buy_cash, SettlementEventType.trade_buy_shares}

    state = account.apply_fills_settlement_aware([buy], data_dir, "20240102", profile=profile.profile_name, prices={"000001.SZ": 10.0}, cost_basis_method="fifo")
    before_cash = state.cash
    state = account.apply_fills_settlement_aware([buy], data_dir, "20240102", profile=profile.profile_name, prices={"000001.SZ": 10.0}, cost_basis_method="fifo")
    assert state.cash == before_cash
    assert len(state.settlement_events) == 2

    assert "000001.SZ" not in state.positions
    state = account.settle("20240103", prices={"000001.SZ": 10.0}, profile=profile.profile_name)
    assert state.positions["000001.SZ"].shares == 100
    assert state.positions["000001.SZ"].available_shares == 100
    assert len(state.position_lots) == 1

    sell = ExecutionFill(
        trade_date="20240103",
        ts_code="000001.SZ",
        side="SELL",
        price=12.0,
        shares=40,
        value=480.0,
        status="FILLED",
        cost=1.0,
        commission=1.0,
        broker_fill_id="bf_sell",
        child_order_id="child_sell",
    )
    state = account.apply_fills_settlement_aware([sell], data_dir, "20240103", profile=profile.profile_name, prices={"000001.SZ": 12.0}, cost_basis_method="fifo")
    assert state.positions["000001.SZ"].shares == 60
    assert state.realized_pnl_ledger
    assert state.realized_pnl_ledger[-1]["shares"] == 40
    assert math.isfinite(state.realized_pnl_ledger[-1]["realized_pnl"])
    cash_before_receivable = state.cash
    state = account.settle("20240104", prices={"000001.SZ": 12.0}, profile=profile.profile_name)
    assert state.cash > cash_before_receivable

    precheck = precheck_orders_against_availability(
        state,
        [ExecutionOrder("20240104", "000001.SZ", "SELL", 0.0, 10_000.0)],
        prices={"000001.SZ": 12.0},
        profile=profile,
    )
    assert precheck["unavailable_share_count"] == 1

    paths = write_settlement_report(state, tmp_path / "settlement", "20240104", prices_by_date={"20240104": {"000001.SZ": 12.0}})
    assert validate_artifact(paths["settlement_report_path"], strict=True).valid is True
    assert validate_artifact(paths["settlement_events_path"], strict=True).valid is True


def test_average_and_fifo_sell_allocation_are_serializable():
    lots = []
    lots = apply_buy_fill_to_lots(
        lots,
        account_id="acct",
        ts_code="000001.SZ",
        source_id="buy_1",
        source_type="trade_fill",
        trade_date="20240102",
        settle_date="20240102",
        available_date="20240103",
        shares=100,
        total_cost=1000.0,
    )
    lots = apply_buy_fill_to_lots(
        lots,
        account_id="acct",
        ts_code="000001.SZ",
        source_id="buy_2",
        source_type="trade_fill",
        trade_date="20240103",
        settle_date="20240103",
        available_date="20240104",
        shares=100,
        total_cost=1200.0,
    )

    fifo_lots, fifo_pnl = allocate_sell_lots(
        lots,
        ts_code="000001.SZ",
        shares=150,
        proceeds=1800.0,
        fee_tax_total=3.0,
        trade_date="20240104",
        sell_fill_id="sell_fifo",
        method="fifo",
    )
    avg_lots, avg_pnl = allocate_sell_lots(
        lots,
        ts_code="000001.SZ",
        shares=150,
        proceeds=1800.0,
        fee_tax_total=3.0,
        trade_date="20240104",
        sell_fill_id="sell_avg",
        method="average",
    )

    assert sum(lot.shares_remaining for lot in fifo_lots) == 50
    assert sum(lot.shares_remaining for lot in avg_lots) == 50
    assert fifo_pnl.cost_basis_method == "fifo"
    assert avg_pnl.cost_basis_method == "average"
    json.dumps(fifo_pnl.to_dict())
