import torch

from factor_store import FactorRecord, LocalFactorStore, stable_formula_hash
from research.composite import (
    build_composite_factor_matrix,
    register_composite_factor,
    select_approved_factors,
)


def _save_factor(store, factor_id, names, values, score=1.0, status="approved"):
    formula_hash = stable_formula_hash([], names, "ashare_features_v1", "ashare_ops_v1")
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=names,
            formula_tokens=[],
            formula_hash=formula_hash,
            feature_version="ashare_features_v1",
            operator_version="ashare_ops_v1",
            lookback_days=1,
            created_at="2026-06-27T00:00:00Z",
            status=status,
            metrics={"score": score},
            factor_type="single",
        )
    )
    store.save_factor_values(factor_id, ["000001.SZ", "600000.SH", "830000.BJ"], ["20240103", "20240104"], values)


def test_composite_factor_methods_and_registration(tmp_path):
    store = LocalFactorStore(tmp_path)
    _save_factor(store, "factor_a", ["A"], torch.tensor([[1.0, 2.0], [2.0, 3.0], [3.0, 4.0]]), score=2.0)
    _save_factor(store, "factor_b", ["B"], torch.tensor([[3.0, 1.0], [2.0, 2.0], [1.0, 3.0]]), score=1.0)

    ts_codes = ["000001.SZ", "600000.SH", "830000.BJ"]
    trade_dates = ["20240103", "20240104"]
    selected = select_approved_factors(store, max_factors=2, max_pairwise_corr=0.99)

    assert selected == ["factor_a", "factor_b"]
    for method in ["equal_weight", "score_weighted", "rank_average"]:
        matrix = build_composite_factor_matrix(store, selected, ts_codes, trade_dates, method)
        assert matrix.shape == (3, 2)
        assert torch.isfinite(matrix).all()

    matrix = build_composite_factor_matrix(store, selected, ts_codes, trade_dates, "rank_average")
    info = register_composite_factor(
        store,
        selected,
        ts_codes,
        trade_dates,
        matrix,
        method="rank_average",
        batch_id="batch_test",
    )
    records = store.load_factors()
    composite = [record for record in records if record.factor_id == info["factor_id"]][0]

    assert composite.status == "approved"
    assert composite.factor_type == "composite"
    assert composite.parent_factor_ids == selected
    assert composite.metadata["component_factor_ids"] == selected
    assert store.load_factor_values(info["factor_id"])


def test_select_approved_factors_filters_pairwise_correlation(tmp_path):
    store = LocalFactorStore(tmp_path)
    values = torch.tensor([[1.0, 2.0], [2.0, 3.0], [3.0, 4.0]])
    _save_factor(store, "factor_a", ["A"], values, score=2.0)
    _save_factor(store, "factor_same", ["SAME"], values, score=1.5)

    selected = select_approved_factors(store, max_factors=2, max_pairwise_corr=0.5)

    assert selected == ["factor_a"]
