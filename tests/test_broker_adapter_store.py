import json

import pytest

from broker_adapter import (
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerStateError,
    LocalBrokerStore,
    SimulatedBrokerAdapter,
    validate_transition,
)


def _request(client_order_id="child_1", shares=100, order_value=1000.0):
    return BrokerOrderRequest(
        client_order_id=client_order_id,
        batch_id="batch_1",
        trade_date="20240104",
        ts_code="000001.SZ",
        side="BUY",
        shares=shares,
        order_value=order_value,
        price=10.0,
        parent_order_id="parent_1",
        child_order_id=client_order_id,
        bucket="open",
    )


def test_broker_models_store_and_state_machine(tmp_path):
    validate_transition(BrokerOrderStatus.NEW, BrokerOrderStatus.SUBMITTED)
    with pytest.raises(BrokerStateError):
        validate_transition(BrokerOrderStatus.FILLED, BrokerOrderStatus.CANCELLED)

    adapter = SimulatedBrokerAdapter(tmp_path, prices={"000001.SZ": 10.0}, volumes={"000001.SZ": 10_000.0})
    result = adapter.submit_orders([_request()], batch_id="batch_1")

    assert result.orders[0].status == BrokerOrderStatus.FILLED
    assert result.fills[0].child_order_id == "child_1"
    json.dumps(result.to_dict())

    replay = adapter.submit_orders([_request()], batch_id="batch_1")
    assert replay.orders[0].broker_order_id == result.orders[0].broker_order_id
    assert replay.idempotent_replay_count == 1
    assert len(LocalBrokerStore(tmp_path).load_fills(batch_id="batch_1")) == 1
    assert (tmp_path / "broker_orders.jsonl").exists()
    assert (tmp_path / "broker_events.jsonl").exists()
    assert (tmp_path / "broker_fills.jsonl").exists()


def test_cancel_replace_only_non_terminal_orders(tmp_path):
    adapter = SimulatedBrokerAdapter(tmp_path, auto_fill=False)
    result = adapter.submit_orders([_request()], batch_id="batch_1")
    order = result.orders[0]

    replaced = adapter.replace_order(order.broker_order_id, shares=200, order_value=2000.0, price=10.0)
    assert replaced.replace_count == 1
    cancelled = adapter.cancel_order(order.broker_order_id, "manual")
    assert cancelled.status == BrokerOrderStatus.CANCELLED

    with pytest.raises(BrokerStateError):
        adapter.cancel_order(order.broker_order_id, "again")
