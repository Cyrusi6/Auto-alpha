import json

from data_pipeline.ashare import AShareDataConfig, LocalAshareStorage, SampleAShareDataProvider


def test_local_storage_writes_jsonl_records(tmp_path):
    config = AShareDataConfig(provider="sample", data_dir=tmp_path)
    records = SampleAShareDataProvider().fetch_securities(config)
    storage = LocalAshareStorage(tmp_path)

    result = storage.write_dataset("securities", records)

    assert result.dataset == "securities"
    assert result.records == len(records)
    lines = (tmp_path / "securities" / "records.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(records)
    assert all(json.loads(line)["ts_code"] for line in lines)
    assert storage.dataset_path("securities") == tmp_path / "securities" / "records.jsonl"
    assert storage.manifest_path == tmp_path / "manifest.json"


def test_local_storage_reads_dataset(tmp_path):
    config = AShareDataConfig(provider="sample", data_dir=tmp_path)
    records = SampleAShareDataProvider().fetch_securities(config)
    storage = LocalAshareStorage(tmp_path)

    storage.write_dataset("securities", records)
    loaded = storage.read_dataset("securities")

    assert [record["ts_code"] for record in loaded] == [record.ts_code for record in records]


def test_local_storage_append_deduplicates_by_dataset_key(tmp_path):
    config = AShareDataConfig(provider="sample", data_dir=tmp_path)
    records = SampleAShareDataProvider().fetch_daily_bars(config)
    storage = LocalAshareStorage(tmp_path)

    first = storage.write_dataset("daily_bars", records, mode="append")
    second = storage.write_dataset("daily_bars", records, mode="append")
    loaded = storage.read_dataset("daily_bars")

    assert first.records == len(records)
    assert second.records == len(records)
    assert len(loaded) == len(records)


def test_local_storage_overwrite_replaces_existing_records(tmp_path):
    config = AShareDataConfig(provider="sample", data_dir=tmp_path)
    records = SampleAShareDataProvider().fetch_securities(config)
    storage = LocalAshareStorage(tmp_path)

    storage.write_dataset("securities", records, mode="overwrite")
    result = storage.write_dataset("securities", records[:1], mode="overwrite")

    assert result.records == 1
    assert len(storage.read_dataset("securities")) == 1


def test_local_storage_writes_manifest_without_secrets(tmp_path):
    config = AShareDataConfig(provider="sample", tushare_token="secret-value", data_dir=tmp_path)
    storage = LocalAshareStorage(tmp_path)
    result = storage.write_dataset("securities", SampleAShareDataProvider().fetch_securities(config))

    manifest = storage.write_manifest(config, [result])

    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    raw = json.dumps(payload)
    assert manifest.dataset == "manifest"
    assert payload["provider"] == "sample"
    assert "TUSHARE_TOKEN" not in raw
    assert "secret-value" not in raw
