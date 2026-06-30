import json

from artifact_schema.validator import validate_artifact
from program_trading_compliance import run_compliance
from program_trading_compliance.secret_scan import scan_artifacts_for_secrets


def test_program_trading_compliance_pack_cli_and_schema(tmp_path, capsys):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / ".env.example").write_text("TUSHARE_TOKEN=\nBROKER_TOKEN=\n", encoding="utf-8")
    (artifact_dir / "risk_control_report.json").write_text(
        '{"kill_switch_available":true,"pre_trade_risk_controls_enabled":true}',
        encoding="utf-8",
    )

    exit_code = run_compliance.main(
        [
            "build-pack",
            "--output-dir",
            str(tmp_path / "compliance"),
            "--artifact-dir",
            str(artifact_dir),
            "--risk-control-report-path",
            str(artifact_dir / "risk_control_report.json"),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["summary"]["real_broker_submit_supported"] is False
    assert payload["summary"]["secret_blocker_count"] == 0

    pack_path = tmp_path / "compliance" / "program_trading_compliance_pack.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    assert pack["artifact_type"] == "program_trading_compliance_pack"
    assert pack["real_broker_submit_supported"] is False
    assert "not legal advice" in pack["legal_notice"]
    assert any(item["evidence_id"] == "no_real_broker_submit_path" for item in pack["evidence_records"])
    assert validate_artifact(pack_path, strict=True).valid is True
    assert validate_artifact(tmp_path / "compliance" / "program_trading_evidence_records.jsonl", strict=True).valid is True


def test_secret_scan_blocks_explicit_non_placeholder_secret(tmp_path):
    secret_file = tmp_path / "operator_note.txt"
    secret_file.write_text("broker_token=super-secret-token-1234567890\n", encoding="utf-8")

    report = scan_artifacts_for_secrets([tmp_path])

    assert report.blocker_count == 1
    assert report.status == "failed"
    assert report.findings[0].severity == "blocker"
