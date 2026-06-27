import json
import math

import torch

from backtest import AShareBacktestSimulator, AShareTradingRules, factor_values_to_matrix, select_factor_id
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
    for key in [
        "total_return",
        "sharpe",
        "max_drawdown",
        "avg_turnover",
        "total_cost",
        "n_trades",
        "rejected_trades",
        "partial_fills",
        "fill_rate",
        "constraint_reject_rate",
        "avg_exposure",
        "cash_drag",
    ]:
        assert key in result.metrics
        assert math.isfinite(result.metrics[key])
    statuses = {fill.status for fill in result.fills}
    assert statuses <= {"FILLED", "PARTIAL", "REJECTED"}


def test_backtest_simulator_applies_limit_and_volume_constraints(tmp_path):
    data_dir, _ = prepare_registered_factor(tmp_path)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    code_index = {ts_code: idx for idx, ts_code in enumerate(loader.ts_codes)}
    factors = torch.zeros((len(loader.ts_codes), len(loader.trade_dates)))
    # Day 1 buys 600000.SH; day 2 tries to sell it while down-limit and buy 000001.SZ while up-limit.
    factors[code_index["600000.SH"], 0] = 10.0
    factors[code_index["000001.SZ"], 1] = 10.0
    factors[code_index["830000.BJ"], 2] = 10.0

    result = AShareBacktestSimulator(
        top_n=1,
        max_weight=0.10,
        trading_rules=AShareTradingRules(max_position_weight=0.10, volume_limit_ratio=0.001),
    ).simulate(factors, loader)

    statuses = {fill.status for fill in result.fills}
    reasons = {fill.reason for fill in result.fills}

    assert "PARTIAL" in statuses
    assert "REJECTED" in statuses
    assert "limit_up" in reasons
    assert "limit_down" in reasons
    assert result.metrics["partial_fills"] > 0
    assert result.metrics["rejected_trades"] > 0
