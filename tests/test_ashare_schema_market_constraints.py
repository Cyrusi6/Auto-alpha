from dataclasses import asdict

from data_pipeline.ashare import AdjustmentFactor, DailyLimit, IndexMember


def test_market_constraint_dataclasses_are_serializable():
    records = [
        DailyLimit("20240102", "000001.SZ", up_limit=10.0, down_limit=9.0, pre_close=9.5),
        AdjustmentFactor("20240102", "000001.SZ", adj_factor=1.02),
        IndexMember("000300.SH", "20240102", "000001.SZ", weight=0.42),
    ]

    payloads = [asdict(record) for record in records]

    assert payloads[0]["up_limit"] == 10.0
    assert payloads[1]["adj_factor"] == 1.02
    assert payloads[2]["index_code"] == "000300.SH"
