import json
from pathlib import Path

from ci.run_local_ci import main as local_ci_main


def test_local_ci_quick_runs_offline(tmp_path, capsys):
    exit_code = local_ci_main(["--quick", "--output-dir", str(tmp_path / "ci"), "--pretty"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "passed"
    assert {item["name"] for item in payload["commands"]} >= {
        "import_smoke",
        "data_source_sample_smoke",
        "artifact_schema_validate",
        "release_manager_dry_run",
    }
    assert (tmp_path / "ci" / "ci_report.json").exists()
    assert (tmp_path / "ci" / "quick_artifacts" / "sample_smoke" / "data_source_smoke_report.json").exists()


def test_workflow_boundaries_are_offline_by_default():
    ci_text = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    release_text = Path(".github/workflows/release-smoke.yml").read_text(encoding="utf-8")
    online_text = Path(".github/workflows/tushare-online-smoke.yml").read_text(encoding="utf-8")

    assert "--allow-network" not in ci_text
    assert "secrets.TUSHARE_TOKEN" not in ci_text
    assert "workflow_dispatch" in release_text
    assert "--allow-network" not in release_text
    assert "workflow_dispatch" in online_text
    assert "secrets.TUSHARE_TOKEN" in online_text
    assert "--allow-network" in online_text
