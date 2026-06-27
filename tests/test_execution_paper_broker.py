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
    assert fills[0].cost > 0
    assert fills[1].status == "REJECTED"
    assert fills[2].status == "REJECTED"
    assert (tmp_path / "paper_fills.jsonl").exists()


def test_paper_broker_applies_market_constraints(tmp_path):
    orders = [
        ExecutionOrder("20240103", "000001.SZ", "BUY", 0.10, 10000.0),
        ExecutionOrder("20240103", "600000.SH", "SELL", 0.00, 10000.0),
        ExecutionOrder("20240103", "830000.BJ", "BUY", 0.10, 10000.0),
        ExecutionOrder("20240103", "000002.SZ", "BUY", 0.10, 10000.0),
    ]

    fills = PaperBroker(tmp_path).submit_orders(
        orders,
        prices={"000001.SZ": 10.0, "600000.SH": 10.0, "830000.BJ": 10.0, "000002.SZ": 10.0},
        trade_date="20240103",
        volumes={"000001.SZ": 100000.0, "600000.SH": 100000.0, "830000.BJ": 2000.0, "000002.SZ": 100000.0},
        suspended={"000002.SZ": True},
        limit_up={"000001.SZ": True},
        limit_down={"600000.SH": True},
    )

    assert fills[0].status == "REJECTED"
    assert fills[0].reason == "limit_up"
    assert fills[1].status == "REJECTED"
    assert fills[1].reason == "limit_down"
    assert fills[2].status == "PARTIAL"
    assert fills[2].reason == "volume_limit_partial"
    assert fills[3].status == "REJECTED"
    assert fills[3].reason == "suspended"
