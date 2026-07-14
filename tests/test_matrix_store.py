import json

import torch

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from data_pipeline.ashare.storage import LocalAshareStorage
from matrix_store import MatrixStoreReader, build_matrix_cache, validate_matrix_cache
from model_core.data_loader import AShareDataLoader
from universe.builder import build_universe_from_storage
from universe.models import UniverseBuildConfig


def _prepare_sample_data(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    build_universe_from_storage(
        LocalAshareStorage(data_dir),
        UniverseBuildConfig(
            universe_name="csi300_sample",
            as_of_date="20240104",
            min_listed_days=0,
            min_amount=0,
            index_code="000300.SH",
            use_index_members=True,
        ),
    )
    return data_dir


def test_build_and_validate_matrix_cache(tmp_path):
    data_dir = _prepare_sample_data(tmp_path)
    result = build_matrix_cache(data_dir, universe_name="csi300_sample")
    report = validate_matrix_cache(result.cache_dir)
    metadata = json.loads((data_dir / "matrix_cache" / "metadata.json").read_text(encoding="utf-8"))

    assert (data_dir / "matrix_cache" / "fields.json").exists()
    assert (data_dir / "matrix_cache" / "ts_codes.json").exists()
    assert (data_dir / "matrix_cache" / "trade_dates.json").exists()
    assert result.n_stocks == 3
    assert result.n_dates == 3
    assert result.cache_hash == metadata["cache_hash"]
    assert report.valid
    assert "adjusted_close" in result.fields
    assert "industry_codes" in result.fields


def test_matrix_reader_matches_jsonl_loader_alignment(tmp_path):
    data_dir = _prepare_sample_data(tmp_path)
    build_matrix_cache(data_dir, universe_name="csi300_sample")
    reader = MatrixStoreReader(data_dir / "matrix_cache")
    json_loader = AShareDataLoader(data_dir=data_dir, device="cpu", universe_name="csi300_sample").load_data()
    raw = reader.to_raw_data_cache(device="cpu")

    assert reader.load_metadata()["n_stocks"] == len(json_loader.ts_codes)
    assert reader.load_ts_codes() == json_loader.ts_codes
    assert reader.load_trade_dates() == json_loader.trade_dates
    assert torch.allclose(raw["adjusted_close"], json_loader.raw_data_cache["adjusted_close"], equal_nan=True)
    assert torch.allclose(raw["roe"], json_loader.raw_data_cache["roe"])
    assert raw["industry_codes"].shape[0] == len(json_loader.ts_codes)


def test_matrix_cache_hash_is_stable_for_same_input(tmp_path):
    data_dir = _prepare_sample_data(tmp_path)
    first = build_matrix_cache(data_dir, universe_name="csi300_sample")
    second = build_matrix_cache(data_dir, universe_name="csi300_sample")

    assert first.cache_hash == second.cache_hash
