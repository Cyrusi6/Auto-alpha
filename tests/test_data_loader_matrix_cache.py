import json
from pathlib import Path

import pytest
import torch

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from matrix_store import build_matrix_cache
from model_core.data_loader import AShareDataLoader


def test_data_loader_uses_matrix_cache_when_requested(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    build_matrix_cache(data_dir)

    json_loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    matrix_loader = AShareDataLoader(
        data_dir=data_dir,
        device="cpu",
        matrix_cache_dir=data_dir / "matrix_cache",
        use_matrix_cache=True,
    ).load_data()

    assert matrix_loader.ts_codes == json_loader.ts_codes
    assert matrix_loader.trade_dates == json_loader.trade_dates
    assert matrix_loader.feat_tensor.shape == json_loader.feat_tensor.shape
    assert matrix_loader.target_ret.shape == json_loader.target_ret.shape
    assert torch.allclose(matrix_loader.raw_data_cache["adjusted_close"], json_loader.raw_data_cache["adjusted_close"])
    assert torch.allclose(matrix_loader.raw_data_cache["limit_up_flag"], json_loader.raw_data_cache["limit_up_flag"])
    assert torch.equal(matrix_loader.industry_codes.cpu(), json_loader.industry_codes.cpu())


def test_data_loader_matrix_cache_missing_error_is_clear(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()

    with pytest.raises(FileNotFoundError, match="matrix cache metadata"):
        AShareDataLoader(data_dir=data_dir, use_matrix_cache=True).load_data()


def test_data_loader_restores_effective_universe_from_matrix_metadata(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    result = build_matrix_cache(data_dir)
    cache_dir = Path(result.cache_dir)
    metadata_path = cache_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["effective_universe_name"] = "csi300_sample"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    loader = AShareDataLoader(
        data_dir=data_dir,
        device="cpu",
        matrix_cache_dir=cache_dir,
        use_matrix_cache=True,
    ).load_data()

    assert loader.universe_name == "csi300_sample"
