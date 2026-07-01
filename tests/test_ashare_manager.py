import json
from pathlib import Path

from data_pipeline.ashare import AShareDataConfig, AShareDataManager, LocalAshareStorage
from data_pipeline.ashare.dataset_registry import FULL_RESEARCH_DATASETS

EXPECTED_CORE_DATASETS = [
    "securities",
    "trade_calendar",
    "daily_bars",
    "daily_basic",
    "financial_features",
    "daily_limits",
    "adjustment_factors",
    "index_members",
    "corporate_actions",
]


def test_ashare_data_manager_sync_writes_all_datasets(tmp_path):
    config = AShareDataConfig(provider="sample", data_dir=tmp_path)
    result = AShareDataManager(config).sync()

    names = [dataset.dataset for dataset in result.datasets]
    assert names == list(FULL_RESEARCH_DATASETS)
    assert set(names) >= set(EXPECTED_CORE_DATASETS)
    for dataset in result.datasets:
        path = Path(dataset.path)
        assert path.is_relative_to(tmp_path)
        assert path.exists()
        assert path.name == "records.jsonl"
    assert Path(result.manifest_path).is_relative_to(tmp_path)
    assert Path(result.manifest_path).exists()
    assert result.state_path is not None
    assert Path(result.state_path).exists()


def test_sync_result_to_dict_is_json_serializable(tmp_path):
    config = AShareDataConfig(provider="sample", data_dir=tmp_path)
    result = AShareDataManager(config).sync()

    encoded = json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True)

    assert json.loads(encoded)["provider"] == "sample"


def test_ashare_data_manager_sync_with_validation_writes_quality_report(tmp_path):
    config = AShareDataConfig(provider="sample", data_dir=tmp_path)
    result = AShareDataManager(config).sync(validate=True)
    payload = result.to_dict()

    assert payload["has_errors"] is False
    assert result.quality_report_path is not None
    assert Path(result.quality_report_path).exists()
    assert payload["quality_summary"]["total_errors"] == 0


def test_ashare_data_manager_append_mode_deduplicates_records(tmp_path):
    config = AShareDataConfig(provider="sample", data_dir=tmp_path)
    manager = AShareDataManager(config)

    manager.sync(mode="append")
    result = manager.sync(mode="append")
    storage = LocalAshareStorage(tmp_path)

    assert len(storage.read_dataset("daily_bars")) == 6
    assert len(storage.read_dataset("daily_limits")) == 6
    assert len(storage.read_dataset("adjustment_factors")) == 6
    assert len(storage.read_dataset("index_members")) == 3
    assert len(storage.read_dataset("corporate_actions")) == 4
    assert next(dataset for dataset in result.datasets if dataset.dataset == "daily_bars").records == 6
