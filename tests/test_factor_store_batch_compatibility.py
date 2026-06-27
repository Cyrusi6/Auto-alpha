import json

import torch

from factor_store import FactorRecord, LocalFactorStore


def test_factor_store_loads_old_records_and_updates_status(tmp_path):
    store = LocalFactorStore(tmp_path)
    store.factor_path.parent.mkdir(parents=True, exist_ok=True)
    old_payload = {
        "factor_id": "factor_old",
        "formula": ["RET_1D"],
        "formula_tokens": [0],
        "formula_hash": "hash_old",
        "feature_version": "ashare_features_v1",
        "operator_version": "ashare_ops_v1",
        "lookback_days": 1,
        "created_at": "2026-06-27T00:00:00Z",
    }
    store.factor_path.write_text(json.dumps(old_payload) + "\n", encoding="utf-8")

    record = store.load_factors()[0]
    result = store.update_factor_status("factor_old", "rejected", reason="manual_review")
    updated = store.load_factors()[0]

    assert record.factor_type is None
    assert record.parent_factor_ids is None
    assert record.batch_id is None
    assert result.records == 1
    assert updated.status == "rejected"
    assert updated.gate_reasons == ["manual_review"]


def test_find_factor_by_hash_and_load_factor_values_matrix(tmp_path):
    store = LocalFactorStore(tmp_path)
    record = FactorRecord(
        factor_id="factor_matrix",
        formula=["RET_1D"],
        formula_tokens=[0],
        formula_hash="hash_matrix",
        feature_version="ashare_features_v1",
        operator_version="ashare_ops_v1",
        lookback_days=1,
        created_at="2026-06-27T00:00:00Z",
    )
    store.save_factor(record)
    store.save_factor_values(
        "factor_matrix",
        ["000001.SZ", "600000.SH"],
        ["20240103", "20240104"],
        torch.tensor([[1.0, 2.0], [3.0, 4.0]]),
    )

    matrix = store.load_factor_values_matrix(
        "factor_matrix",
        ["600000.SH", "000001.SZ"],
        ["20240104", "20240103"],
    )

    assert store.find_factor_by_hash("hash_matrix").factor_id == "factor_matrix"
    assert matrix.tolist() == [[4.0, 3.0], [2.0, 1.0]]
