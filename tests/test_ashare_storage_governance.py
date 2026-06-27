import json

from data_pipeline.ashare import AShareDataConfig, LocalAshareStorage, SampleAShareDataProvider
from data_pipeline.ashare.stats import compute_dataset_stats, write_dataset_stats


def test_storage_compact_snapshot_index_and_stats(tmp_path):
    config = AShareDataConfig(provider="sample", data_dir=tmp_path)
    provider = SampleAShareDataProvider()
    storage = LocalAshareStorage(tmp_path)
    records = provider.fetch_daily_bars(config)
    storage.write_dataset("daily_bars", [*records, records[0]], mode="overwrite")

    assert len(storage.read_dataset("daily_bars")) == len(records) + 1
    compacted = storage.compact_dataset("daily_bars")
    snapshot_path = storage.snapshot_dataset("daily_bars", snapshot_name="snap_test")
    index_path = storage.build_record_index("daily_bars")
    index = storage.read_dataset_index("daily_bars")
    stats = compute_dataset_stats(storage, "daily_bars")
    stats_path = write_dataset_stats([stats], tmp_path / "dataset_stats.json")

    assert compacted.records == len(records)
    assert snapshot_path == tmp_path / "snapshots" / "snap_test" / "daily_bars" / "records.jsonl"
    assert snapshot_path.exists()
    assert index_path.exists()
    assert index["000001.SZ|20240102"] >= 0
    assert stats.records == len(records)
    assert stats.unique_keys == len(records)
    assert stats.duplicate_keys == 0
    assert stats.ts_code_count == 3
    assert json.loads(stats_path.read_text(encoding="utf-8"))["datasets"][0]["dataset"] == "daily_bars"
