import json
from pathlib import Path

from broker_file_gateway.run_gateway import main as gateway_main
from broker_file_gateway.state import LocalBrokerFileGatewayStore


def test_broker_file_gateway_smoke_roundtrip(tmp_path: Path) -> None:
    output = tmp_path / "gateway"
    code = gateway_main(
        [
            "smoke",
            "--gateway-store-dir",
            str(output),
            "--output-dir",
            str(output),
            "--outbox-dir",
            str(output / "outbox"),
            "--inbox-dir",
            str(output / "inbox"),
            "--trade-date",
            "20240104",
        ]
    )

    assert code == 0
    assert (output / "outbox" / "broker_orders.csv").exists()
    assert (output / "outbox" / "broker_file_manifest.json").exists()
    assert (output / "outbox" / "broker_file_checksum_manifest.json").exists()
    assert (output / "broker_file_gateway_report.json").exists()
    assert (output / "broker_file_roundtrip_report.json").exists()
    report = json.loads((output / "broker_file_gateway_report.json").read_text(encoding="utf-8"))
    assert report["summary"]["no_real_submit"] is True
    assert report["summary"]["file_outbox_real_submit_detected"] is False
    assert report["summary"]["broker_file_roundtrip_error_count"] == 0


def test_broker_file_gateway_idempotent_batch(tmp_path: Path) -> None:
    output = tmp_path / "gateway"
    args = [
        "export-outbox",
        "--gateway-store-dir",
        str(output),
        "--output-dir",
        str(output),
        "--outbox-dir",
        str(output / "outbox"),
        "--approval-id",
        "approval_1",
        "--production-run-id",
        "prod_1",
        "--trade-date",
        "20240104",
    ]
    assert gateway_main(args) == 0
    assert gateway_main(args) == 0
    batches = LocalBrokerFileGatewayStore(output).list_batches()
    assert len(batches) == 1
    assert batches[0].approval_id == "approval_1"
