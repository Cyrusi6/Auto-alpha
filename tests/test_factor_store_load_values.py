from factor_store import FactorValueRecord, LocalFactorStore


def test_load_factor_values_after_save(tmp_path):
    store = LocalFactorStore(tmp_path)
    factor_id = "factor_1234567890abcdef"

    store.save_factor_values(
        factor_id,
        ["000001.SZ", "600000.SH"],
        ["20240102", "20240103"],
        [[1.0, 2.0], [3.0, None]],
    )
    records = store.load_factor_values(factor_id)

    assert len(records) == 4
    assert isinstance(records[0], FactorValueRecord)
    assert records[0].factor_id == factor_id
    assert store.load_factor_values("factor_missing") == []
