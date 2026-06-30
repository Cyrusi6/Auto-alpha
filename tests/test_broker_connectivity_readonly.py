import json
from pathlib import Path

from artifact_schema.validator import validate_artifact
from approval.run_approval import main as approval_main
from broker_connectivity.models import BrokerConnectivityBlockedError, PROHIBITED_METHODS
from broker_connectivity.network_guard import build_network_guard, enforce_readonly_method
from broker_connectivity.profiles import build_broker_connection_profile
from broker_connectivity.run_connectivity import main as connectivity_main
from broker_readonly_mirror.run_readonly_mirror import main as readonly_mirror_main
from broker_uat_lab.run_uat import main as uat_main


def test_mock_readonly_connectivity_review_probe_and_schema(tmp_path, capsys):
    approval_dir = tmp_path / "approvals"
    review_dir = tmp_path / "review"
    assert connectivity_main(
        [
            "create-review",
            "--profile-name",
            "mock_readonly",
            "--output-dir",
            str(review_dir),
            "--approval-store-dir",
            str(approval_dir),
            "--reviewer",
            "unit",
            "--pretty",
        ]
    ) == 0
    approval_id = json.loads(capsys.readouterr().out)["approval_id"]
    assert approval_main(["--store-dir", str(approval_dir), "approve", "--approval-id", approval_id, "--reviewer", "unit"]) == 0
    capsys.readouterr()

    output_dir = tmp_path / "connectivity"
    assert connectivity_main(
        [
            "probe",
            "--profile-name",
            "mock_readonly",
            "--output-dir",
            str(output_dir),
            "--connectivity-store-dir",
            str(tmp_path / "connectivity_store"),
            "--approval-store-dir",
            str(approval_dir),
            "--approval-id",
            approval_id,
            "--require-approval",
            "--trade-date",
            "20240104",
            "--as-of-date",
            "20240104",
        ]
    ) == 0
    summary = json.loads(capsys.readouterr().out)
    report_path = Path(summary["paths"]["broker_connectivity_report_path"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["summary"]["readonly_only"] is True
    assert report["summary"]["real_submit_supported"] is False
    assert "submit_orders" in report["summary"]["prohibited_submit_blocked"].__str__() or report["summary"]["prohibited_submit_blocked"] is True
    assert validate_artifact(report_path, strict=True).valid is True
    assert validate_artifact(output_dir / "broker_credential_ref_manifest.json", strict=True).valid is True


def test_network_guard_blocks_prohibited_methods():
    profile = build_broker_connection_profile("mock_readonly")
    guard = build_network_guard(profile, allow_network=False)
    for method in PROHIBITED_METHODS:
        try:
            enforce_readonly_method(profile, guard, method)
        except BrokerConnectivityBlockedError:
            pass
        else:
            raise AssertionError(f"prohibited method was not blocked: {method}")


def test_readonly_mirror_from_connectivity_report_and_uat_readonly_scenarios(tmp_path, capsys):
    conn_dir = tmp_path / "connectivity"
    assert connectivity_main(["probe", "--profile-name", "mock_readonly", "--output-dir", str(conn_dir), "--trade-date", "20240104", "--as-of-date", "20240104"]) == 0
    capsys.readouterr()
    mirror_dir = tmp_path / "mirror"
    assert readonly_mirror_main(
        [
            "snapshot",
            "--connectivity-report-path",
            str(conn_dir / "broker_connectivity_report.json"),
            "--output-dir",
            str(mirror_dir),
            "--mirror-store-dir",
            str(tmp_path / "mirror_store"),
            "--trade-date",
            "20240104",
            "--as-of-date",
            "20240104",
        ]
    ) == 0
    mirror_summary = json.loads(capsys.readouterr().out)
    assert mirror_summary["status"] == "success"
    assert mirror_summary["readonly_position_count"] == 2
    assert mirror_summary["readonly_mirror_break_count"] == 0
    assert validate_artifact(mirror_dir / "broker_readonly_mirror_report.json", strict=True).valid is True
    assert validate_artifact(mirror_dir / "readonly_mirror_reconciliation_report.json", strict=True).valid is True

    uat_dir = tmp_path / "uat"
    assert uat_main(
        [
            "run",
            "--output-dir",
            str(uat_dir),
            "--broker-store-dir",
            str(tmp_path / "uat_broker"),
            "--profile",
            "sample",
            "--adapter",
            "mock",
            "--connectivity-profile",
            "mock_readonly",
            "--run-readonly-connectivity",
            "--run-readonly-mirror",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "passed"
    assert payload["summary"]["readonly_connectivity_scenario_count"] == 4
    assert (uat_dir / "broker_connectivity" / "broker_connectivity_report.json").exists()
    assert (uat_dir / "broker_readonly_mirror" / "broker_readonly_mirror_report.json").exists()
