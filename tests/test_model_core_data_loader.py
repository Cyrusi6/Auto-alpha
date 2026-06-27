import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from model_core.data_loader import AShareDataLoader


def write_sample_data(data_dir):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()


def test_ashare_data_loader_loads_sample_data(tmp_path):
    write_sample_data(tmp_path)

    loader = AShareDataLoader(data_dir=tmp_path, device="cpu").load_data()

    assert set(loader.ts_codes) == {"000001.SZ", "600000.SH", "830000.BJ"}
    assert loader.feat_tensor.shape[0] == 3
    assert loader.feat_tensor.shape[2] == len(loader.trade_dates)
    assert loader.target_ret.shape == (3, len(loader.trade_dates))
    for old_key in ["liquidity", "fdv", "address"]:
        assert old_key not in loader.raw_data_cache


def test_loader_does_not_use_future_financial_records(tmp_path):
    write_sample_data(tmp_path)
    path = tmp_path / "financial_features" / "records.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "ts_code": "000001.SZ",
                    "report_period": "20241231",
                    "announce_date": "20250101",
                    "roe": 9.99,
                    "revenue_yoy": 8.88,
                }
            )
        )
        handle.write("\n")

    loader = AShareDataLoader(data_dir=tmp_path, device="cpu").load_data()
    stock_idx = loader.ts_codes.index("000001.SZ")
    first_date_idx = 0

    assert loader.raw_data_cache["roe"][stock_idx, first_date_idx].item() != 9.99
