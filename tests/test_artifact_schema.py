import json

from artifact_schema.manifest import build_artifact_manifest, write_artifact_manifest
from artifact_schema.run_validate import main as validate_main
from artifact_schema.validator import validate_artifact
from artifact_schema.writer import write_json_artifact, write_jsonl_artifact


def test_json_artifact_strict_and_legacy_validation(tmp_path):
    path = tmp_path / "capacity_report.json"
    write_json_artifact(
        path,
        {"trade_date": "20240104", "config": {}, "portfolio": {}},
        artifact_type="capacity_report",
        producer="test",
    )

    result = validate_artifact(path, strict=True)

    assert result.valid is True
    assert result.compatibility_mode == "strict"
    assert not [issue for issue in result.issues if issue.severity == "error"]

    legacy = tmp_path / "legacy" / "capacity_report.json"
    legacy.parent.mkdir()
    legacy.write_text('{"trade_date":"20240104","config":{},"portfolio":{}}', encoding="utf-8")
    legacy_result = validate_artifact(legacy)

    assert legacy_result.valid is True
    assert legacy_result.compatibility_mode == "legacy"
    assert any(issue.code == "legacy_artifact" for issue in legacy_result.issues)


def test_jsonl_sidecar_manifest_and_malformed_errors(tmp_path):
    orders_path = tmp_path / "orders.jsonl"
    write_jsonl_artifact(
        orders_path,
        [{"trade_date": "20240104", "ts_code": "000001.SZ", "side": "BUY"}],
        artifact_type="orders",
        producer="test",
    )

    result = validate_artifact(orders_path, strict=True)
    manifest = build_artifact_manifest([orders_path], root_dir=tmp_path)
    manifest_json, manifest_md = write_artifact_manifest(manifest, tmp_path / "manifest")

    assert result.valid is True
    assert (tmp_path / "orders.jsonl.schema.json").exists()
    assert manifest.entries[0].record_count == 1
    assert manifest.entries[0].sha256
    assert manifest_json.exists()
    assert manifest_md.exists()

    bad_json = tmp_path / "capacity_report.json"
    bad_json.write_text("{", encoding="utf-8")
    bad_result = validate_artifact(bad_json)
    assert bad_result.valid is False
    assert any(issue.code == "malformed_json" for issue in bad_result.issues)

    bad_jsonl = tmp_path / "paper_fills.jsonl"
    bad_jsonl.write_text("{not-json}\n", encoding="utf-8")
    bad_jsonl_result = validate_artifact(bad_jsonl)
    assert bad_jsonl_result.valid is False
    assert any(issue.code == "malformed_jsonl" for issue in bad_jsonl_result.issues)


def test_run_validate_cli_scans_dirs_and_catalog(tmp_path, capsys):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    write_json_artifact(
        artifact_dir / "monitoring_report.json",
        {"as_of_date": "20240104", "checks": {}, "alerts": []},
        artifact_type="monitoring_report",
        producer="test",
    )
    catalog_path = artifact_dir / "artifact_catalog.json"
    write_json_artifact(
        catalog_path,
        {
            "suite_name": "test_suite",
            "created_at": "2026-06-28T00:00:00Z",
            "entries": [{"name": "monitoring", "path": str(artifact_dir / "monitoring_report.json"), "kind": "json", "stage": "test"}],
        },
        artifact_type="artifact_catalog",
        producer="test",
    )

    exit_code = validate_main(
        [
            "--artifact-dir",
            str(artifact_dir),
            "--artifact-catalog-path",
            str(catalog_path),
            "--output-dir",
            str(tmp_path / "schema"),
            "--write-manifest",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["artifact_count"] >= 2
    assert (tmp_path / "schema" / "artifact_validation_report.json").exists()
    assert (tmp_path / "schema" / "artifact_schema_manifest.json").exists()
