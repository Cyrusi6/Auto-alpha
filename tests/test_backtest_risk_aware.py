import json

import torch

from backtest import run_backtest
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_store import FactorRecord, LocalFactorStore
from model_core.data_loader import AShareDataLoader


def _prepare_factor(tmp_path):
    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store = LocalFactorStore(store_dir)
    factor_id = "factor_backtest"
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=["RET_1D"],
            formula_tokens=[0],
            formula_hash="hash_backtest",
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


def test_backtest_risk_aware_outputs_risk_metrics(tmp_path, capsys):
    data_dir, store_dir, factor_id = _prepare_factor(tmp_path)
    output_dir = tmp_path / "backtest"
    risk_dir = tmp_path / "risk"

    result = run_backtest.main(
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
            "--risk-report-dir",
            str(risk_dir),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["portfolio_method"] == "risk_aware"
    assert "tracking_error" in payload["metrics"]
    assert "avg_active_share" in payload["metrics"]
    assert "risk_constraint_violations" in payload["metrics"]
    assert (risk_dir / "risk_report.json").exists()
    assert (output_dir / "optimization_result.json").exists()


def test_backtest_factor_risk_model_outputs_exposure_and_attribution(tmp_path, capsys):
    data_dir, store_dir, factor_id = _prepare_factor(tmp_path)
    output_dir = tmp_path / "backtest_factor_risk"
    risk_dir = tmp_path / "risk_factor_model"

    result = run_backtest.main(
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
            "--use-factor-risk-model",
            "--risk-model-lookback",
            "3",
            "--attribution",
            "--risk-report-dir",
            str(risk_dir),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert "avg_factor_risk" in payload["metrics"]
    assert "max_active_style_exposure_abs" in payload["metrics"]
    assert (risk_dir / "risk_model_report.json").exists()
    assert (output_dir / "risk_exposures.jsonl").exists()
    assert (output_dir / "risk_decomposition.jsonl").exists()
    assert (output_dir / "return_attribution.jsonl").exists()
