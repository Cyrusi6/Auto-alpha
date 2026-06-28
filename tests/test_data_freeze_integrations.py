import json

from data_source_validation.run_smoke import main as smoke_main
from matrix_store.run_build_matrix import main as matrix_main
from monitoring.run_monitor import main as monitor_main


ALL_DATASETS = "securities,trade_calendar,daily_bars,daily_basic,financial_features,daily_limits,adjustment_factors,index_members,corporate_actions"


def test_data_source_smoke_can_write_version_and_freeze(tmp_path, capsys):
    data_dir = tmp_path / "data"
    smoke_dir = tmp_path / "smoke"
    freeze_dir = tmp_path / "freeze"

    assert smoke_main(
        [
            "--provider",
            "sample",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(smoke_dir),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--datasets",
            ALL_DATASETS,
            "--index-codes",
            "000300.SH",
            "--validate",
            "--stats",
            "--write-data-version",
            "--create-research-freeze",
            "--data-lake-registry-dir",
            str(tmp_path / "registry"),
            "--freeze-dir",
            str(freeze_dir),
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["data_lake_summary"]["dataset_version_id"].startswith("dsver_")
    assert payload["data_lake_summary"]["data_freeze_id"].startswith("freeze_")
    assert payload["data_lake_summary"]["data_hash_drift_count"] == 0
    assert (smoke_dir / "data_lake" / "dataset_version_manifest.json").exists()
    assert (freeze_dir / "research_data_freeze.json").exists()


def test_matrix_cache_can_build_from_research_freeze(tmp_path, capsys):
    data_dir = tmp_path / "data"
    smoke_dir = tmp_path / "smoke"
    freeze_dir = tmp_path / "freeze"
    assert smoke_main(
        [
            "--provider",
            "sample",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(smoke_dir),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--datasets",
            ALL_DATASETS,
            "--index-codes",
            "000300.SH",
            "--validate",
            "--stats",
            "--write-data-version",
            "--create-research-freeze",
            "--freeze-dir",
            str(freeze_dir),
        ]
    ) == 0
    capsys.readouterr()

    assert matrix_main(
        [
            "--data-dir",
            str(data_dir),
            "--data-freeze-dir",
            str(freeze_dir),
            "--data-version-manifest-path",
            str(smoke_dir / "data_lake" / "dataset_version_manifest.json"),
            "--output-dir",
            str(tmp_path / "matrix"),
            "--require-data-freeze",
            "--write-matrix-version-manifest",
            "--validate",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["n_stocks"] == 3
    assert payload["n_dates"] == 3
    assert payload["matrix_version_manifest_path"].endswith("matrix_version_manifest.json")


def test_monitoring_reads_backfill_and_data_lake_artifacts(tmp_path, capsys):
    data_dir = tmp_path / "data"
    smoke_dir = tmp_path / "smoke"
    freeze_dir = tmp_path / "freeze"
    assert smoke_main(
        [
            "--provider",
            "sample",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(smoke_dir),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--datasets",
            ALL_DATASETS,
            "--validate",
            "--stats",
            "--write-data-version",
            "--create-research-freeze",
            "--freeze-dir",
            str(freeze_dir),
        ]
    ) == 0
    capsys.readouterr()
    backfill_report = tmp_path / "backfill_run_report.json"
    backfill_report.write_text(
        '{"plan_id":"p1","provider":"sample","status":"success","jobs":[],"summary":{"failed_jobs":0,"success_jobs":1}}',
        encoding="utf-8",
    )
    coverage_report = tmp_path / "backfill_coverage_report.json"
    coverage_report.write_text('{"datasets":[],"gap_count":0,"status":"ok"}', encoding="utf-8")

    rc = monitor_main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--paper-account-dir",
            str(tmp_path / "account"),
            "--orders-dir",
            str(tmp_path / "orders"),
            "--output-dir",
            str(tmp_path / "monitoring"),
            "--as-of-date",
            "20240104",
            "--backfill-run-report-path",
            str(backfill_report),
            "--backfill-coverage-report-path",
            str(coverage_report),
            "--dataset-version-manifest-path",
            str(smoke_dir / "data_lake" / "dataset_version_manifest.json"),
            "--freeze-validation-report-path",
            str(smoke_dir / "data_lake" / "freeze_validation_report.json"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc in {0, 1}
    assert payload["checks"]["backfill_run"]["backfill_status"] == "success"
    assert payload["checks"]["data_lake_version"]["dataset_version_id"].startswith("dsver_")
    assert payload["data_hash_drift_count"] == 0
