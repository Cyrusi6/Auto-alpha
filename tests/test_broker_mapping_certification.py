import json
from pathlib import Path

from broker_mapping_certification.run_mapping_certify import main as certify_main


def test_mapping_certification_certifies_generic_profile(tmp_path: Path) -> None:
    output = tmp_path / "mapping"
    code = certify_main(["--output-dir", str(output), "--profile-name", "generic_broker_csv", "--policy", "dry_run_standard"])

    assert code == 0
    decision = json.loads((output / "broker_mapping_certification_decision.json").read_text(encoding="utf-8"))
    assert decision["status"] == "certified_for_dry_run"
    assert decision["checks"]["roundtrip_error_count"] == 0
    assert decision["checks"]["no_real_submit"] is True
    assert (output / "broker_mapping_certification_report.md").exists()


def test_qmt_skeleton_notice_is_explicit(tmp_path: Path) -> None:
    output = tmp_path / "mapping_qmt"
    assert certify_main(["--output-dir", str(output), "--profile-name", "qmt_skeleton_csv", "--policy", "dry_run_standard"]) == 0
    decision = json.loads((output / "broker_mapping_certification_decision.json").read_text(encoding="utf-8"))
    assert decision["status"] == "certified_for_dry_run"
    assert "does not guarantee compatibility" in decision["qmt_skeleton_notice"]
