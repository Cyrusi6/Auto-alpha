import json

from broker_adapter import (
    BrokerAdapterConfig,
    BrokerOrderRequest,
    FileInstructionBrokerAdapter,
    SimulatedBrokerAdapter,
    broker_fills_to_execution_fills,
)
from execution import ExecutionFill


def _request(client_order_id, ts_code="000001.SZ", side="BUY", shares=100, price=10.0, order_value=1000.0):
    return BrokerOrderRequest(
        client_order_id=client_order_id,
        batch_id="batch_1",
        trade_date="20240104",
        ts_code=ts_code,
        side=side,
        shares=shares,
        order_value=order_value,
        price=price,
        parent_order_id="parent_1",
        child_order_id=client_order_id,
        bucket="open",
    )


def test_simulated_broker_fill_partial_and_rejects(tmp_path):
    adapter = SimulatedBrokerAdapter(
        tmp_path,
        prices={"000001.SZ": 10.0, "600000.SH": 10.0, "830000.BJ": 10.0},
        volumes={"000001.SZ": 10_000.0, "600000.SH": 5_000.0, "830000.BJ": 10_000.0},
        suspended={"830000.BJ": True},
        limit_up={"000001.SZ": False, "600000.SH": False},
    )
    result = adapter.submit_orders(
        [
            _request("child_fill", "000001.SZ", shares=100),
            _request("child_partial", "600000.SH", shares=1000, order_value=10000.0),
            _request("child_reject", "830000.BJ", shares=100),
        ],
        batch_id="batch_1",
    )
    statuses = {fill.child_order_id: fill.status for fill in result.fills}

    assert statuses["child_fill"] == "FILLED"
    assert statuses["child_partial"] == "PARTIAL"
    assert statuses["child_reject"] == "REJECTED"
    execution_fills = broker_fills_to_execution_fills(result.fills)
    assert all(isinstance(fill, ExecutionFill) for fill in execution_fills)
    assert execution_fills[0].broker_order_id
    assert execution_fills[0].broker_fill_id
    assert execution_fills[0].commission >= 0
    assert execution_fills[0].cost_breakdown is not None


def test_file_instruction_adapter_exports_and_imports_inbox(tmp_path):
    outbox = tmp_path / "outbox"
    adapter = FileInstructionBrokerAdapter(
        tmp_path / "store",
        outbox,
        config=BrokerAdapterConfig(
            adapter_type="file",
            schema_name="qmt_skeleton",
            field_mapping={"ts_code": "证券代码", "side": "买卖方向"},
        ),
    )
    result = adapter.submit_orders([_request("child_file")], batch_id="batch_1")

    assert result.fills == []
    assert (outbox / "broker_orders.csv").exists()
    assert (outbox / "broker_orders.jsonl").exists()
    manifest = json.loads((outbox / "broker_instruction_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_name"] == "qmt_skeleton"
    assert "validate field mapping manually" in manifest["notice"]

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "broker_fills.jsonl").write_text(
        json.dumps(
            {
                "client_order_id": "child_file",
                "trade_date": "20240104",
                "ts_code": "000001.SZ",
                "side": "BUY",
                "price": 10.0,
                "shares": 100,
                "value": 1000.0,
                "cost": 5.0,
                "commission": 4.0,
                "transfer_fee": 1.0,
                "status": "FILLED",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    adapter_with_inbox = FileInstructionBrokerAdapter(tmp_path / "store", outbox, inbox)
    imported = adapter_with_inbox.submit_orders([_request("child_file")], batch_id="batch_1")

    assert imported.idempotent_replay_count == 1
    assert imported.fills[0].status == "FILLED"
    assert imported.fills[0].commission == 4.0
    assert imported.fills[0].transfer_fee == 1.0
