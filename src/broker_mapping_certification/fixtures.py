"""Small deterministic order fixtures for mapping certification."""

from __future__ import annotations

from typing import Any


def sample_child_orders(trade_date: str = "20240104") -> list[dict[str, Any]]:
    return [
        {
            "child_order_id": f"cert_{trade_date}_buy_open",
            "parent_order_id": f"cert_{trade_date}_buy",
            "trade_date": trade_date,
            "ts_code": "000001.SZ",
            "side": "BUY",
            "bucket": "open",
            "order_value": 12000.0,
            "target_weight": 0.05,
            "price": 10.0,
            "reason": "mapping_certification",
        },
        {
            "child_order_id": f"cert_{trade_date}_sell_close",
            "parent_order_id": f"cert_{trade_date}_sell",
            "trade_date": trade_date,
            "ts_code": "600000.SH",
            "side": "SELL",
            "bucket": "close",
            "order_value": 8000.0,
            "target_weight": 0.02,
            "price": 12.5,
            "reason": "mapping_certification",
        },
    ]
