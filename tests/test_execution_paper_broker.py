from execution import ExecutionOrder, PaperBroker, export_orders_csv, export_orders_jsonl


def test_export_orders_and_paper_broker(tmp_path):
    orders = [
        ExecutionOrder(
            trade_date="20240102",
            ts_code="000001.SZ",
            side="BUY",
            target_weight=0.10,
            order_value=10000.0,
        ),
        ExecutionOrder(
            trade_date="20240102",
            ts_code="600000.SH",
            side="BUY",
            target_weight=0.10,
            order_value=10000.0,
        ),
        ExecutionOrder(
            trade_date="20240102",
            ts_code="830000.BJ",
            side="BUY",
            target_weight=0.10,
            order_value=10000.0,
        ),
    ]

    csv_path = export_orders_csv(orders, tmp_path / "orders.csv")
    jsonl_path = export_orders_jsonl(orders, tmp_path / "orders.jsonl")
    fills = PaperBroker(tmp_path).submit_orders(
        orders,
        {"000001.SZ": 10.0, "600000.SH": 0.0},
        "20240102",
    )

    assert csv_path.exists()
    assert jsonl_path.exists()
    assert fills[0].status == "FILLED"
    assert fills[1].status == "REJECTED"
    assert fills[2].status == "REJECTED"
    assert (tmp_path / "paper_fills.jsonl").exists()
