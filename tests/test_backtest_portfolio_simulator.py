import json
import math

from backtest import AShareBacktestSimulator, factor_values_to_matrix, select_factor_id
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_store import LocalFactorStore
from model_core import engine
from model_core.data_loader import AShareDataLoader


def prepare_registered_factor(tmp_path):
    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()
    engine.main(
        [
            "--dry-run",
            "--register",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--factor-store-dir",
            str(store_dir),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )
    return data_dir, store_dir


def test_backtest_simulator_from_registered_factor(tmp_path, capsys):
    data_dir, store_dir = prepare_registered_factor(tmp_path)
    capsys.readouterr()
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store = LocalFactorStore(store_dir)
    factor_id = select_factor_id(store)
    factors = factor_values_to_matrix(store.load_factor_values(factor_id), loader.ts_codes, loader.trade_dates)

    result = AShareBacktestSimulator(top_n=2, max_weight=0.10).simulate(factors, loader)
    payload = result.to_dict()

    json.dumps(payload)
    assert len(result.snapshots) == len(loader.trade_dates)
    for key in ["total_return", "sharpe", "max_drawdown", "avg_turnover", "total_cost", "n_trades"]:
        assert key in result.metrics
        assert math.isfinite(result.metrics[key])
