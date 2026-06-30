import json
from pathlib import Path

from artifact_schema.run_validate import main as validate_main
from broker_file_gateway.run_gateway import main as gateway_main
from broker_mapping_certification.run_mapping_certify import main as certify_main
from live_readiness.run_readiness import main as readiness_main
from monitoring.run_monitor import main as monitor_main
from operator_handoff.run_handoff import main as handoff_main


def test_file_outbox_readiness_and_monitoring(tmp_path: Path) -> None:
    gateway = tmp_path / "gateway"
    handoff = tmp_path / "handoff"
    mapping = tmp_path / "mapping"
    replay = tmp_path / "production_replay_report.json"
    readiness = tmp_path / "readiness"
    monitoring = tmp_path / "monitoring"
    data_dir = tmp_path / "data"
    factor_store = tmp_path / "store"
    account = tmp_path / "account"
    orders = tmp_path / "orders"
    for directory in [data_dir / "trade_calendar", factor_store, account, orders]:
        directory.mkdir(parents=True, exist_ok=True)
    (data_dir / "trade_calendar" / "records.jsonl").write_text('{"trade_date":"20240104","is_open":true}\n', encoding="utf-8")

    assert gateway_main(["smoke", "--gateway-store-dir", str(gateway), "--output-dir", str(gateway), "--outbox-dir", str(gateway / "outbox"), "--inbox-dir", str(gateway / "inbox")]) == 0
    assert handoff_main(["smoke", "--handoff-store-dir", str(handoff), "--output-dir", str(handoff), "--file-batch-id", "file_batch_1", "--approval-id", "approval_1"]) == 0
    assert certify_main(["--output-dir", str(mapping), "--profile-name", "generic_broker_csv"]) == 0
    replay.write_text(
        json.dumps(
            {
                "replay_id": "replay_file",
                "status": "success",
                "summary": {
                    "replay_day_count": 1,
                    "replay_failed_day_count": 0,
                    "replay_blocked_day_count": 0,
                    "file_outbox_day_count": 1,
                    "file_outbox_real_submit_detected": False,
                },
            }
        ),
        encoding="utf-8",
    )

    assert readiness_main(
        [
            "run",
            "--policy-profile",
            "file_outbox_dry_run_strict",
            "--production-replay-report-path",
            str(replay),
            "--broker-mapping-certification-decision-path",
            str(mapping / "broker_mapping_certification_decision.json"),
            "--broker-file-gateway-report-path",
            str(gateway / "broker_file_gateway_report.json"),
            "--operator-handoff-report-path",
            str(handoff / "operator_handoff_report.json"),
            "--output-dir",
            str(readiness),
        ]
    ) == 0
    decision = json.loads((readiness / "live_readiness_decision.json").read_text(encoding="utf-8"))
    assert decision["new_status"] == "ready_for_file_outbox_dry_run"

    assert monitor_main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(factor_store),
            "--paper-account-dir",
            str(account),
            "--orders-dir",
            str(orders),
            "--output-dir",
            str(monitoring),
            "--as-of-date",
            "20240104",
            "--broker-file-gateway-report-path",
            str(gateway / "broker_file_gateway_report.json"),
            "--operator-handoff-report-path",
            str(handoff / "operator_handoff_report.json"),
            "--broker-mapping-certification-decision-path",
            str(mapping / "broker_mapping_certification_decision.json"),
            "--live-readiness-decision-path",
            str(readiness / "live_readiness_decision.json"),
            "--live-readiness-scorecard-path",
            str(readiness / "live_readiness_scorecard.json"),
        ]
    ) == 0
    report = json.loads((monitoring / "monitoring_report.json").read_text(encoding="utf-8"))
    assert report["broker_file_roundtrip_error_count"] == 0
    assert report["operator_handoff_missing_required_count"] == 0
    assert report["broker_mapping_certification_status"] == "certified_for_dry_run"

    assert validate_main(["--artifact-dir", str(gateway), "--artifact-dir", str(handoff), "--artifact-dir", str(mapping), "--output-dir", str(tmp_path / "schema")]) == 0
