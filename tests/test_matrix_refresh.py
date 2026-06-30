import json

from data_lake.run_lake import main as lake_main
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from matrix_refresh.run_matrix_refresh import main as refresh_main
from matrix_store import build_matrix_cache


DATASETS = "securities,trade_calendar,daily_bars,daily_basic,financial_features,daily_limits,adjustment_factors,index_members,corporate_actions"


def _prepare_versioned_sample(tmp_path, capsys):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    assert lake_main(
        [
            "create-version",
            "--data-dir",
            str(data_dir),
            "--registry-dir",
            str(tmp_path / "registry"),
            "--output-dir",
            str(tmp_path / "version"),
            "--provider",
            "sample",
            "--profile-name",
            "sample_offline_small",
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--datasets",
            DATASETS,
        ]
    ) == 0
    capsys.readouterr()
    return data_dir, tmp_path / "version" / "dataset_version_manifest.json"


def test_matrix_refresh_skips_fresh_cache_with_data_version_manifest(tmp_path, capsys):
    data_dir, manifest_path = _prepare_versioned_sample(tmp_path, capsys)
    cache_dir = tmp_path / "matrix_cache"
    build_matrix_cache(data_dir, output_dir=cache_dir, data_version_manifest_path=manifest_path)

    rc = refresh_main(
        [
            "refresh",
            "--data-dir",
            str(data_dir),
            "--data-version-manifest-path",
            str(manifest_path),
            "--matrix-cache-dir",
            str(cache_dir),
            "--output-dir",
            str(tmp_path / "refresh"),
            "--refresh-mode",
            "skip_if_fresh",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["action"] == "skip"
    assert payload["status"] == "fresh"
    assert payload["source_diff"]["drift_count"] == 0
    assert (tmp_path / "refresh" / "matrix_refresh_result.json").exists()
    assert (tmp_path / "refresh" / "matrix_freshness_report.json").exists()


def test_matrix_refresh_full_rebuild_writes_artifacts(tmp_path, capsys):
    data_dir, manifest_path = _prepare_versioned_sample(tmp_path, capsys)

    rc = refresh_main(
        [
            "refresh",
            "--data-dir",
            str(data_dir),
            "--data-version-manifest-path",
            str(manifest_path),
            "--matrix-cache-dir",
            str(tmp_path / "rebuilt_cache"),
            "--output-dir",
            str(tmp_path / "refresh"),
            "--refresh-mode",
            "full_rebuild",
            "--point-in-time",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["action"] == "full_rebuild"
    assert payload["status"] in {"refreshed", "fresh", "validated"}
    assert (tmp_path / "rebuilt_cache" / "metadata.json").exists()
    assert (tmp_path / "refresh" / "matrix_source_diff.json").exists()
