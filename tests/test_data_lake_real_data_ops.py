from data_lake.models import DatasetVersionRecord
from data_lake.registry import LocalDataLakeRegistry


def test_data_lake_latest_real_data_profile_and_promotion(tmp_path):
    registry = LocalDataLakeRegistry(tmp_path / "registry")
    record = DatasetVersionRecord(
        dataset_version_id="dsver_unit_real",
        provider="tushare",
        data_dir=str(tmp_path / "data"),
        start_date="20240102",
        end_date="20240104",
        datasets=["daily_bars"],
        dataset_fingerprints=[],
        status="validated",
        content_hash="hash_unit_real",
        data_version_status="validated",
        provider_profile="tushare_online_smoke",
        real_data_profile_id="rdp_unit",
        real_data_sla_status="pass",
        matrix_cache_dir=str(tmp_path / "matrix_cache"),
        matrix_refresh_report_path=str(tmp_path / "matrix_refresh_result.json"),
        real_data_size_report_path=str(tmp_path / "real_data_size_report.json"),
        latest_trade_date="20240104",
    )

    registry.register_dataset_version(record)

    latest = registry.latest_validated_real_data(provider="tushare")
    assert latest is not None
    assert latest.provider_profile == "tushare_online_smoke"
    assert latest.matrix_cache_dir.endswith("matrix_cache")

    promoted = registry.promote_dataset_version("dsver_unit_real", "frozen")
    assert promoted is not None
    assert promoted.data_version_status == "frozen"
    assert registry.latest_validated_real_data(provider="tushare").status == "frozen"
