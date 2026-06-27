import json
from pathlib import Path

from data_pipeline.ashare import AShareDataConfig, AShareDataManager


def test_ashare_data_manager_sync_writes_all_datasets(tmp_path):
    config = AShareDataConfig(provider="sample", data_dir=tmp_path)
    result = AShareDataManager(config).sync()

    assert [dataset.dataset for dataset in result.datasets] == [
        "securities",
        "trade_calendar",
        "daily_bars",
        "daily_basic",
        "financial_features",
    ]
    for dataset in result.datasets:
        path = Path(dataset.path)
        assert path.is_relative_to(tmp_path)
        assert path.exists()
        assert path.name == "records.jsonl"
    assert Path(result.manifest_path).is_relative_to(tmp_path)
    assert Path(result.manifest_path).exists()


def test_sync_result_to_dict_is_json_serializable(tmp_path):
    config = AShareDataConfig(provider="sample", data_dir=tmp_path)
    result = AShareDataManager(config).sync()

    encoded = json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True)

    assert json.loads(encoded)["provider"] == "sample"
