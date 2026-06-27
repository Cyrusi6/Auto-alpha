import json

import torch

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_store import FactorRecord, LocalFactorStore
from model_core.data_loader import AShareDataLoader
from portfolio_optimizer import OptimizationConfig, PortfolioOptimizer
from portfolio_optimizer import run_optimize
from risk_model import benchmark_weights_from_index_members, estimate_return_covariance


def _prepare_data_and_factor(tmp_path):
    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store = LocalFactorStore(store_dir)
    factor_id = "factor_test"
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=["RET_1D"],
            formula_tokens=[0],
            formula_hash="hash_test",
            feature_version="test",
            operator_version="test",
            lookback_days=1,
            created_at="2026-06-27T00:00:00Z",
            status="approved",
            factor_type="composite",
            parent_factor_ids=["factor_a", "factor_b"],
        )
    )
    values = torch.arange(len(loader.ts_codes) * len(loader.trade_dates), dtype=torch.float32).reshape(
        len(loader.ts_codes),
        len(loader.trade_dates),
    )
    store.save_factor_values(factor_id, loader.ts_codes, loader.trade_dates, values)
    return data_dir, store_dir, loader, factor_id


def test_portfolio_optimizer_constraints_are_deterministic(tmp_path):
    _, _, loader, _ = _prepare_data_and_factor(tmp_path)
    benchmark = benchmark_weights_from_index_members(loader, "000300.SH", "20240104")
    cov = estimate_return_covariance(loader)
    alpha = torch.tensor([1.0, 3.0, 2.0])
    config = OptimizationConfig(max_weight=0.10, max_names=2, max_turnover=0.05, max_tracking_error=1.0)

    first = PortfolioOptimizer(config).optimize(alpha, torch.zeros(3), benchmark, cov, loader)
    second = PortfolioOptimizer(config).optimize(alpha, torch.zeros(3), benchmark, cov, loader)

    assert first.to_dict() == second.to_dict()
    assert sum(first.weights.values()) <= 1.0
    assert all(weight <= 0.10 + 1e-9 for weight in first.weights.values())
    assert len(first.weights) <= 2
    assert first.turnover <= 0.05 + 1e-6


def test_run_optimize_cli_writes_artifacts(tmp_path, capsys):
    data_dir, store_dir, _, factor_id = _prepare_data_and_factor(tmp_path)
    output_dir = tmp_path / "optimize"

    result = run_optimize.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(output_dir),
            "--factor-id",
            factor_id,
            "--index-code",
            "000300.SH",
            "--as-of-date",
            "20240104",
            "--max-weight",
            "0.10",
            "--max-names",
            "2",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["factor_id"] == factor_id
    assert (output_dir / "optimized_weights.jsonl").exists()
    assert (output_dir / "optimization_result.json").exists()
    assert (output_dir / "risk_report.json").exists()
