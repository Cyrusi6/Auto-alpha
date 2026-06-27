import torch

from factor_engine.correlation import (
    factor_correlation,
    factor_correlation_matrix,
    find_similar_factors,
    load_existing_factor_matrices,
    max_abs_correlation,
    pairwise_correlation_table,
)
from factor_store import FactorRecord, LocalFactorStore


def test_factor_correlation_detects_positive_and_negative_relation():
    x = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
    y = x * 2.0
    z = -x

    assert factor_correlation(x, y) > 0.99
    assert factor_correlation(x, z) < -0.99


def test_max_abs_correlation_returns_largest_absolute_value():
    candidate = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
    unrelated = torch.tensor([[1.0, -1.0], [-1.0, 1.0]])
    same = candidate.clone()

    assert max_abs_correlation(candidate, [unrelated, same]) > 0.99


def test_correlation_matrix_and_pairwise_table_are_serializable():
    matrices = {
        "factor_a": torch.tensor([[1.0, 2.0], [3.0, 4.0]]),
        "factor_b": torch.tensor([[2.0, 4.0], [6.0, 8.0]]),
        "factor_c": torch.tensor([[4.0, 3.0], [2.0, 1.0]]),
    }

    matrix = factor_correlation_matrix(matrices)
    table = pairwise_correlation_table(matrices)

    assert matrix["factor_a"]["factor_b"] > 0.99
    assert matrix["factor_a"]["factor_c"] < -0.99
    assert table[0]["abs_correlation"] > 0.99


def test_load_existing_factor_matrices_aligns_store_values(tmp_path):
    store = LocalFactorStore(tmp_path)
    factor_id = "factor_abc"
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=["RET_1D"],
            formula_tokens=[0],
            formula_hash="abc",
            feature_version="v1",
            operator_version="v1",
            lookback_days=1,
            created_at="2026-06-27T00:00:00Z",
        )
    )
    store.save_factor_values(
        factor_id,
        ts_codes=["000001.SZ", "600000.SH"],
        trade_dates=["20240102", "20240103"],
        values=torch.tensor([[1.0, 2.0], [3.0, 4.0]]),
    )

    matrices = load_existing_factor_matrices(
        store,
        [factor_id],
        ts_codes=["600000.SH", "000001.SZ"],
        trade_dates=["20240103", "20240102"],
    )

    assert matrices[factor_id].tolist() == [[4.0, 3.0], [2.0, 1.0]]


def test_find_similar_factors_identifies_threshold_matches(tmp_path):
    store = LocalFactorStore(tmp_path)
    factor_id = "factor_abc"
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=["RET_1D"],
            formula_tokens=[0],
            formula_hash="abc",
            feature_version="v1",
            operator_version="v1",
            lookback_days=1,
            created_at="2026-06-27T00:00:00Z",
        )
    )
    values = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
    store.save_factor_values(factor_id, ["000001.SZ", "600000.SH"], ["20240102", "20240103"], values)

    similar = find_similar_factors(
        values,
        store,
        ts_codes=["000001.SZ", "600000.SH"],
        trade_dates=["20240102", "20240103"],
        threshold=0.99,
    )

    assert similar[0]["factor_id"] == factor_id
    assert similar[0]["abs_correlation"] > 0.99
