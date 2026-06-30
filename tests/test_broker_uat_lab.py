import json

from artifact_schema.validator import validate_artifact
from broker_adapter import BrokerOrderRequest
from broker_uat_lab.mock_broker import DeterministicMockBrokerAdapter
from broker_uat_lab.run_uat import main as uat_main
from broker_uat_lab.scenarios import build_default_uat_scenarios


def _request(client_order_id="child_uat_1"):
    return BrokerOrderRequest(
        client_order_id=client_order_id,
        batch_id="uat_batch",
        trade_date="20240104",
        ts_code="000001.SZ",
        side="BUY",
        shares=100,
        order_value=1000.0,
        price=10.0,
        price_type="MARKET",
        parent_order_id="parent_uat_1",
        child_order_id=client_order_id,
        bucket="open",
    )


def test_default_uat_scenarios_are_sample_sized():
    scenarios = build_default_uat_scenarios("sample")

    assert 10 <= len(scenarios) <= 15
    assert {scenario.scenario_type for scenario in scenarios} >= {"submit_idempotency", "full_fill", "kill_switch_block"}


def test_deterministic_mock_broker_idempotency_and_reject(tmp_path):
    adapter = DeterministicMockBrokerAdapter(tmp_path, scenario_type="full_fill")
    first = adapter.submit_orders([_request()], batch_id="uat_batch")
    replay = adapter.submit_orders([_request()], batch_id="uat_batch")

    assert first.orders[0].broker_order_id == replay.orders[0].broker_order_id
    assert replay.idempotent_replay_count == 1
    assert len(adapter.list_fills(batch_id="uat_batch")) == 1

    adapter.set_scenario("kill_switch_block")
    rejected = adapter.submit_orders([_request("child_uat_2")], batch_id="uat_batch")

    assert rejected.orders[0].status == "REJECTED"
    assert rejected.fills[0].reason == "risk_kill_switch_active"


def test_broker_uat_cli_writes_registered_artifacts(tmp_path, capsys):
    exit_code = uat_main(
        [
            "run",
            "--output-dir",
            str(tmp_path / "uat"),
            "--broker-store-dir",
            str(tmp_path / "broker_store"),
            "--profile",
            "sample",
            "--adapter",
            "mock",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "passed"
    assert payload["summary"]["failed_count"] == 0
    for filename in [
        "broker_uat_report.json",
        "broker_adapter_contract_report.json",
        "broker_adapter_capability_manifest.json",
        "broker_uat_results.jsonl",
    ]:
        assert validate_artifact(tmp_path / "uat" / filename, strict=True).valid is True
