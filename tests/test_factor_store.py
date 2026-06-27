import json

import torch

from factor_store import (
    ExperimentRecord,
    FactorRecord,
    LocalFactorStore,
    make_factor_id,
    stable_formula_hash,
)


def test_factor_hash_and_id_are_stable():
    formula_tokens = [0, 1, 11]
    formula = ["RET_1D", "RET_5D", "ADD"]

    first = stable_formula_hash(formula_tokens, formula, "features_v1", "ops_v1")
    second = stable_formula_hash(formula_tokens, formula, "features_v1", "ops_v1")

    assert first == second
    assert make_factor_id(first) == f"factor_{first[:16]}"


def test_local_factor_store_roundtrip_and_values(tmp_path):
    store = LocalFactorStore(tmp_path)
    factor = FactorRecord(
        factor_id="factor_1234567890abcdef",
        formula=["RET_1D"],
        formula_tokens=[0],
        formula_hash="1234567890abcdef" * 4,
        feature_version="ashare_features_v1",
        operator_version="ashare_ops_v1",
        lookback_days=1,
        created_at="2026-06-27T00:00:00Z",
        metrics={"score": 1.0},
    )
    experiment = ExperimentRecord(
        experiment_id="exp_1234567890abcdef_20260627T000000Z",
        factor_id=factor.factor_id,
        data_dir="data/ashare",
        output_dir="out",
        train_dates=["20240102"],
        valid_dates=["20240103"],
        test_dates=["20240104"],
        metrics_by_split={"all": {"score": 1.0}},
        created_at="2026-06-27T00:00:00Z",
    )

    factor_result = store.save_factor(factor)
    experiment_result = store.save_experiment(experiment)
    value_result = store.save_factor_values(
        factor.factor_id,
        ["000001.SZ", "600000.SH"],
        ["20240102", "20240103"],
        torch.tensor([[1.0, 2.0], [3.0, 4.0]]),
    )

    assert factor_result.records == 1
    assert experiment_result.records == 1
    assert value_result.records == 4
    assert store.load_factors() == [factor]
    assert store.load_experiments() == [experiment]
    value_lines = (tmp_path / "factor_values" / f"{factor.factor_id}.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(value_lines[0])["ts_code"] == "000001.SZ"

    written = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.jsonl"))
    for forbidden in ["TUSHARE_TOKEN", "secret", "meme", "crypto", "solana", "birdeye", "dexscreener"]:
        assert forbidden not in written.lower()
