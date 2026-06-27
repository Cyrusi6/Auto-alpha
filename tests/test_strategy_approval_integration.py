import json

from approval import LocalApprovalStore
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_store import FactorRecord, LocalFactorStore, stable_formula_hash
from model_core.data_loader import AShareDataLoader
from strategy_manager import runner


def test_strategy_propose_only_writes_approval_batch_without_fills(tmp_path, capsys):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store = LocalFactorStore(tmp_path / "store")
    factor_id = "factor_strategy_approval"
    formula_tokens = [0]
    formula_names = ["RET_1D"]
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=formula_names,
            formula_tokens=formula_tokens,
            formula_hash=stable_formula_hash(formula_tokens, formula_names, "ashare_features_v1", "ashare_ops_v1"),
            feature_version="ashare_features_v1",
            operator_version="ashare_ops_v1",
            lookback_days=1,
            created_at="2026-06-27T00:00:00Z",
            status="production_candidate",
            factor_type="composite",
        )
    )
    values = [[float(stock_idx + date_idx) for date_idx, _date in enumerate(loader.trade_dates)] for stock_idx, _code in enumerate(loader.ts_codes)]
    store.save_factor_values(factor_id, loader.ts_codes, loader.trade_dates, values)

    exit_code = runner.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--output-dir",
            str(tmp_path / "orders"),
            "--factor-id",
            factor_id,
            "--rebalance-date",
            "20240104",
            "--top-n",
            "2",
            "--max-weight",
            "0.10",
            "--propose-only",
            "--approval-store-dir",
            str(tmp_path / "approvals"),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["approval_id"]
    assert payload["approval_status"] == "pending"
    assert payload["propose_only"] is True
    assert not (tmp_path / "orders" / "paper_fills.jsonl").exists()
    assert LocalApprovalStore(tmp_path / "approvals").load_batch(payload["approval_id"]).status == "pending"
