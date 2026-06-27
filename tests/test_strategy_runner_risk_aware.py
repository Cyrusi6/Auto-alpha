import json

import torch

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_store import FactorRecord, LocalFactorStore
from model_core.data_loader import AShareDataLoader
from strategy_manager import runner


def _prepare_factor(tmp_path):
    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store = LocalFactorStore(store_dir)
    factor_id = "factor_strategy"
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=["RET_1D"],
            formula_tokens=[0],
            formula_hash="hash_strategy",
            feature_version="test",
            operator_version="test",
            lookback_days=1,
            created_at="2026-06-27T00:00:00Z",
            status="approved",
            factor_type="composite",
        )
    )
    values = torch.tensor([[1.0, 1.0, 1.0], [3.0, 3.0, 3.0], [2.0, 2.0, 2.0]])
    store.save_factor_values(factor_id, loader.ts_codes, loader.trade_dates, values)
    return data_dir, store_dir, factor_id


def test_strategy_runner_risk_aware_exports_optimized_targets(tmp_path, capsys):
    data_dir, store_dir, factor_id = _prepare_factor(tmp_path)
    output_dir = tmp_path / "orders"

    result = runner.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(output_dir),
            "--factor-id",
            factor_id,
            "--portfolio-method",
            "risk_aware",
            "--index-code",
            "000300.SH",
            "--top-n",
            "2",
            "--max-weight",
            "0.10",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    target_payload = json.loads((output_dir / "target_positions.jsonl").read_text(encoding="utf-8").splitlines()[0])

    assert result == 0
    assert payload["portfolio_method"] == "risk_aware"
    assert payload["risk_metrics"]
    assert "benchmark_weight" in target_payload
    assert "active_weight" in target_payload
    assert (output_dir / "risk_report.json").exists()
    assert (output_dir / "optimization_result.json").exists()
