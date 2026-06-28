import json
from pathlib import Path

from artifact_schema.run_validate import main as validate_artifacts_main
from data_backfill.run_backfill import main as backfill_main
from data_lake.run_lake import main as lake_main
from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService


DATASETS = "securities,trade_calendar,daily_bars,daily_basic,financial_features,daily_limits,adjustment_factors,index_members,corporate_actions"


def test_backfill_execute_writes_governed_artifacts(tmp_path, capsys):
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "backfill"

    assert backfill_main(
        [
            "execute",
            "--provider",
            "sample",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(output_dir),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--datasets",
            DATASETS,
            "--index-codes",
            "000300.SH",
            "--chunk-days",
            "2",
            "--validate",
            "--stats",
            "--compact",
            "--snapshot",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "success"
    assert payload["summary"]["failed_jobs"] == 0
    assert payload["summary"]["coverage_gap_count"] == 0
    assert (output_dir / "backfill_plan.json").exists()
    assert (output_dir / "backfill_run_report.json").exists()
    assert (output_dir / "backfill_coverage_report.json").exists()
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    assert {item["dataset"] for item in manifest["datasets"]} >= {"securities", "daily_bars", "index_members"}


def test_data_lake_version_freeze_lineage_and_schema_validation(tmp_path, capsys):
    data_dir = tmp_path / "data"
    backfill_dir = tmp_path / "backfill"
    registry_dir = tmp_path / "registry"
    version_dir = tmp_path / "version"
    freeze_dir = tmp_path / "freeze"

    assert backfill_main(
        [
            "execute",
            "--provider",
            "sample",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(backfill_dir),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--datasets",
            DATASETS,
            "--index-codes",
            "000300.SH",
            "--validate",
            "--stats",
        ]
    ) == 0
    capsys.readouterr()

    assert lake_main(
        [
            "create-version",
            "--data-dir",
            str(data_dir),
            "--registry-dir",
            str(registry_dir),
            "--output-dir",
            str(version_dir),
            "--provider",
            "sample",
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--datasets",
            DATASETS,
            "--backfill-run-report-path",
            str(backfill_dir / "backfill_run_report.json"),
            "--backfill-coverage-report-path",
            str(backfill_dir / "backfill_coverage_report.json"),
        ]
    ) == 0
    version_payload = json.loads(capsys.readouterr().out)
    version_id = version_payload["dataset_version_id"]

    assert lake_main(
        [
            "create-freeze",
            "--data-dir",
            str(data_dir),
            "--registry-dir",
            str(registry_dir),
            "--output-dir",
            str(tmp_path / "freeze_out"),
            "--freeze-dir",
            str(freeze_dir),
            "--dataset-version-id",
            version_id,
            "--freeze-name",
            "unit_freeze",
        ]
    ) == 0
    freeze_payload = json.loads(capsys.readouterr().out)
    assert freeze_payload["freeze_id"].startswith("freeze_")

    assert lake_main(
        [
            "validate-freeze",
            "--registry-dir",
            str(registry_dir),
            "--output-dir",
            str(tmp_path / "validate"),
            "--freeze-dir",
            str(freeze_dir),
            "--fail-on-error",
        ]
    ) == 0
    validation = json.loads(capsys.readouterr().out)
    assert validation["error_count"] == 0

    assert lake_main(
        [
            "lineage",
            "--registry-dir",
            str(registry_dir),
            "--output-dir",
            str(tmp_path / "lineage"),
            "--freeze-dir",
            str(freeze_dir),
            "--artifact-dir",
            str(backfill_dir),
        ]
    ) == 0
    lineage = json.loads(capsys.readouterr().out)
    assert lineage["nodes"] >= 2

    assert validate_artifacts_main(
        [
            "--artifact-dir",
            str(backfill_dir),
            "--artifact-dir",
            str(version_dir),
            "--artifact-dir",
            str(freeze_dir),
            "--output-dir",
            str(tmp_path / "schema"),
            "--write-manifest",
        ]
    ) == 0
    schema_payload = json.loads(capsys.readouterr().out)
    assert schema_payload["error_count"] == 0

    service = AshareDashboardService(
        DashboardConfig(
            data_dir=data_dir,
            factor_store_dir=tmp_path / "store",
            report_dir=tmp_path / "reports",
            backtest_dir=tmp_path / "backtest",
            orders_dir=tmp_path / "orders",
            backfill_dir=backfill_dir,
            data_lake_dir=version_dir,
        )
    )
    assert service.load_backfill_run_report()["status"] == "success"
    assert service.load_dataset_version_manifest()["dataset_version_id"] == version_id
