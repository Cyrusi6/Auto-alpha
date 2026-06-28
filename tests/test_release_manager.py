import json

from artifact_schema.writer import write_json_artifact
from release_manager.inventory import (
    build_cli_inventory,
    build_dependency_inventory,
    build_module_inventory,
)
from release_manager.run_release import main as release_main


def test_release_inventories_cover_platform_modules():
    dependency = build_dependency_inventory(".")
    module_inventory = build_module_inventory(".")
    cli_inventory = build_cli_inventory(".")

    file_names = {item["path"] for item in dependency.files}
    module_names = {item["module"] for item in module_inventory.modules}
    cli_modules = {item["module"] for item in cli_inventory.entries}

    assert "pyproject.toml" in file_names
    assert "data_pipeline" in module_names
    assert "research_suite" in module_names
    assert "broker_adapter" in module_names
    assert "model_registry" in module_names
    assert "factor_lifecycle" in module_names
    assert "assets" not in module_names
    assert "paper" not in module_names
    assert "lord" not in module_names
    assert "times.py" not in module_names
    assert "data_pipeline.run_pipeline" in cli_modules
    assert "model_core.engine" in cli_modules


def test_release_run_writes_reports_without_network(tmp_path, capsys):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    write_json_artifact(
        artifact_dir / "monitoring_report.json",
        {"as_of_date": "20240104", "checks": {}, "alerts": []},
        artifact_type="monitoring_report",
        producer="test",
    )

    exit_code = release_main(
        [
            "--release-name",
            "unit_release",
            "--output-dir",
            str(tmp_path / "release"),
            "--artifact-dir",
            str(artifact_dir),
            "--run-import-smoke",
            "--run-dashboard-import",
            "--run-schema-validation",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    gate = json.loads((tmp_path / "release" / "release_gate_report.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["paths"]["release_manifest_path"].endswith("release_manifest.json")
    assert (tmp_path / "release" / "dependency_inventory.json").exists()
    assert (tmp_path / "release" / "module_inventory.json").exists()
    assert (tmp_path / "release" / "cli_inventory.json").exists()
    assert (tmp_path / "release" / "release_notes_draft.md").exists()
    assert any(check["name"] == "no_real_network_by_default" and check["status"] == "passed" for check in gate["checks"])
    assert any(check["name"] == "artifact_schema_validation" for check in gate["checks"])


def test_pyproject_packaging_config_excludes_non_platform_dirs():
    text = open("pyproject.toml", encoding="utf-8").read()

    assert "build-system" in text
    assert "hatchling" in text
    assert "package = true" in text
    assert '"artifact_schema"' in text
    assert '"release_manager"' in text
    assert '"model_registry"' in text
    assert '"factor_lifecycle"' in text
    assert '"times.py"' in text
    assert '"tests"' in text
