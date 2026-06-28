from __future__ import annotations

import json

from shadow_trading import run_shadow_trading


def test_shadow_trading_uses_child_orders_without_account_side_effects(tmp_path):
    plan_dir = tmp_path / "orders" / "plan"
    plan_dir.mkdir(parents=True)
    child_order = {
        "child_order_id": "child_1",
        "parent_order_id": "parent_1",
        "trade_date": "20240104",
        "ts_code": "000001.SZ",
        "side": "BUY",
        "order_value": 10000.0,
        "target_weight": 0.01,
        "bucket": "open",
    }
    (plan_dir / "child_orders.jsonl").write_text(json.dumps(child_order, ensure_ascii=False) + "\n", encoding="utf-8")

    report = run_shadow_trading(
        production_run_id="prod_shadow_test",
        trade_date="20240104",
        as_of_date="20240104",
        orders_dir=tmp_path / "orders",
        execution_plan_dir=plan_dir,
        output_dir=tmp_path / "shadow",
    )

    assert report.status == "success"
    assert report.summary["shadow_order_count"] == 1
    assert report.summary["shadow_fill_rate"] == 1.0
    assert (tmp_path / "shadow" / "shadow_run_report.json").exists()
    assert (tmp_path / "shadow" / "shadow_fills.jsonl").exists()
    assert not (tmp_path / "account" / "account_state.json").exists()
