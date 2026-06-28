from broker_adapter import BrokerOrderRequest, SimulatedBrokerAdapter
from risk_controls.kill_switch import activate_kill_switch


def test_simulated_broker_rejects_when_risk_kill_switch_active(tmp_path):
    state_dir = tmp_path / "risk_state"
    activate_kill_switch(state_dir, "block broker", actor="tester")
    adapter = SimulatedBrokerAdapter(
        tmp_path / "broker",
        prices={"000001.SZ": 10.0},
        volumes={"000001.SZ": 10000.0},
        risk_control_state_dir=state_dir,
    )
    request = BrokerOrderRequest(
        client_order_id="co_1",
        batch_id="batch_1",
        trade_date="20240104",
        ts_code="000001.SZ",
        side="BUY",
        shares=100,
        order_value=1000.0,
        price=10.0,
    )
    result = adapter.submit_orders([request], batch_id="batch_1")
    assert result.orders[0].status == "REJECTED"
    assert result.fills[0].status == "REJECTED"
    assert result.fills[0].reason == "risk_kill_switch_active"
