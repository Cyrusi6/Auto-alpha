import json

from data_lake.run_lake import main as lake_main
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from matrix_refresh.run_matrix_refresh import main as refresh_main
from matrix_refresh.planner import build_matrix_refresh_plan
from matrix_store import build_matrix_cache
from feature_factory import FEATURE_SET_V3, build_feature_set_manifest


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


def test_matrix_refresh_detects_feature_set_hash_drift(tmp_path, capsys):
    data_dir, manifest_path = _prepare_versioned_sample(tmp_path, capsys)
    feature_manifest = build_feature_set_manifest(FEATURE_SET_V3, feature_set_version="1.0", created_at="2026-01-01T00:00:00Z")
    feature_manifest_path = tmp_path / "feature_set_manifest.json"
    feature_manifest_path.write_text(json.dumps(feature_manifest.to_dict(), ensure_ascii=False), encoding="utf-8")
    cache_dir = tmp_path / "matrix_cache_v3"
    build_matrix_cache(
        data_dir,
        output_dir=cache_dir,
        data_version_manifest_path=manifest_path,
        feature_set_name=FEATURE_SET_V3,
        feature_set_manifest_path=feature_manifest_path,
    )
    changed_manifest = build_feature_set_manifest(FEATURE_SET_V3, feature_set_version="1.1", created_at="2026-01-01T00:00:00Z")
    changed_path = tmp_path / "feature_set_manifest_changed.json"
    changed_path.write_text(json.dumps(changed_manifest.to_dict(), ensure_ascii=False), encoding="utf-8")

    plan = build_matrix_refresh_plan(
        data_dir=data_dir,
        matrix_cache_dir=cache_dir,
        data_version_manifest_path=manifest_path,
        feature_set_name=FEATURE_SET_V3,
        feature_set_manifest_path=changed_path,
    )

    assert plan.recommendation == "full_rebuild"
    assert "feature_set_hash_drift" in plan.reasons
