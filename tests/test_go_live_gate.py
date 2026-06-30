import json

from approval import ApprovalType, LocalApprovalStore
from artifact_schema.validator import validate_artifact
from broker_uat_lab.run_uat import main as uat_main
from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from go_live_gate.models import GoLiveGateStatus
from go_live_gate.run_go_live import main as go_live_main
from program_trading_compliance import run_compliance

FORBIDDEN_STATUS_TEXT = ("ready_for_live_trading", "ready_for_real_broker", "ready_for_auto_submit")


def _build_prereq_artifacts(tmp_path, capsys):
    root = tmp_path / "production"
    uat_main(
        [
            "run",
            "--output-dir",
            str(root / "broker_uat"),
            "--broker-store-dir",
            str(tmp_path / "broker_store"),
            "--profile",
            "sample",
            "--adapter",
            "mock",
        ]
    )
    capsys.readouterr()
    run_compliance.main(
        [
            "build-pack",
            "--output-dir",
            str(root / "compliance"),
            "--artifact-dir",
            str(root / "broker_uat"),
        ]
    )
    capsys.readouterr()
    return root


def test_go_live_gate_cli_allows_only_local_review_statuses(tmp_path, capsys):
    root = _build_prereq_artifacts(tmp_path, capsys)
    approval_dir = tmp_path / "approvals"

    exit_code = go_live_main(
        [
            "run",
            "--policy-profile",
            "sample_lenient_go_live",
            "--output-dir",
            str(root / "go_live_gate"),
            "--program-trading-compliance-pack-path",
            str(root / "compliance" / "program_trading_compliance_pack.json"),
            "--secret-scan-report-path",
            str(root / "compliance" / "secret_scan_report.json"),
            "--broker-uat-report-path",
            str(root / "broker_uat" / "broker_uat_report.json"),
            "--broker-adapter-contract-report-path",
            str(root / "broker_uat" / "broker_adapter_contract_report.json"),
            "--approval-store-dir",
            str(approval_dir),
            "--create-review-approval",
            "--reviewer",
            "unit_reviewer",
            "--comment",
            "local review only",
            "--pretty",
        ]
    )
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)

    assert exit_code == 0
    assert payload["status"] in {
        GoLiveGateStatus.not_ready,
        GoLiveGateStatus.ready_for_broker_uat,
        GoLiveGateStatus.ready_for_file_outbox_dry_run,
        GoLiveGateStatus.ready_for_manual_pilot_review,
        GoLiveGateStatus.insufficient_data,
    }
    assert payload["status"] == GoLiveGateStatus.ready_for_broker_uat
    assert payload["metadata"]["real_broker_submit_enabled"] is False
    assert not any(text in stdout for text in FORBIDDEN_STATUS_TEXT)

    decision_path = root / "go_live_gate" / "go_live_gate_decision.json"
    decision_text = decision_path.read_text(encoding="utf-8")
    assert not any(text in decision_text for text in FORBIDDEN_STATUS_TEXT)
    assert validate_artifact(decision_path, strict=True).valid is True
    assert validate_artifact(root / "go_live_gate" / "go_live_gate_checks.jsonl", strict=True).valid is True

    approvals = LocalApprovalStore(approval_dir).list_batches()
    assert len(approvals) == 1
    assert approvals[0].approval_type == ApprovalType.go_live_review
    assert approvals[0].go_live_status == GoLiveGateStatus.ready_for_broker_uat


def test_dashboard_service_reads_pre_live_artifacts(tmp_path, capsys):
    root = _build_prereq_artifacts(tmp_path, capsys)
    go_live_main(
        [
            "run",
            "--policy-profile",
            "sample_lenient_go_live",
            "--output-dir",
            str(root / "go_live_gate"),
            "--program-trading-compliance-pack-path",
            str(root / "compliance" / "program_trading_compliance_pack.json"),
            "--secret-scan-report-path",
            str(root / "compliance" / "secret_scan_report.json"),
            "--broker-uat-report-path",
            str(root / "broker_uat" / "broker_uat_report.json"),
            "--broker-adapter-contract-report-path",
            str(root / "broker_uat" / "broker_adapter_contract_report.json"),
        ]
    )
    capsys.readouterr()

    service = AshareDashboardService(DashboardConfig(production_dir=root))

    assert service.load_program_trading_compliance_pack()["real_broker_submit_supported"] is False
    assert service.load_broker_uat_report()["status"] == "passed"
    assert service.load_go_live_gate_decision()["status"] == GoLiveGateStatus.ready_for_broker_uat
    assert not service.load_broker_uat_results().empty
    assert not service.load_go_live_gate_checks().empty
