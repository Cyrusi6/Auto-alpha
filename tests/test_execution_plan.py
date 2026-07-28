import json

from backtest import AShareCostModel, AShareTradingRules
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from execution import ExecutionOrder
from execution_plan import (
    ExecutionPlanConfig,
    ExecutionPlanResult,
    build_execution_schedule,
    build_parent_orders_from_target_orders,
    simulate_child_orders,
    write_execution_plan_report,
)
from model_core.data_loader import AShareDataLoader


def _loader(tmp_path):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=tmp_path)).sync()
    return AShareDataLoader(data_dir=tmp_path, device="cpu").load_data()


def test_parent_child_schedule_and_quality_report(tmp_path):
    loader = _loader(tmp_path / "data")
    orders = [
        ExecutionOrder("20240104", "000001.SZ", "BUY", 0.1, 80_000.0),
        ExecutionOrder("20240104", "600000.SH", "BUY", 0.1, 60_000.0),
    ]
    config = ExecutionPlanConfig(buckets=("open",), max_child_participation=0.10)
    parents = build_parent_orders_from_target_orders(orders)
    schedule, capacity = build_execution_schedule(parents, loader, "20240104", config)
    simulated = simulate_child_orders(schedule, loader, AShareCostModel(), AShareTradingRules())
    result = ExecutionPlanResult(schedule, simulated.fills, simulated.quality, capacity.to_dict())
    paths = write_execution_plan_report(result, tmp_path / "plan")

    assert len(parents) == 2
    assert schedule.child_orders
    assert all(order.order_value <= capacity.records[0].max_trade_value for order in schedule.child_orders if order.ts_code == "000001.SZ")
    assert simulated.quality.child_order_count == len(schedule.child_orders)
    assert simulated.quality.execution_fill_rate >= 0
    assert paths["execution_plan_path"].exists()
    assert paths["child_orders_path"].exists()
    assert json.loads(paths["execution_quality_path"].read_text(encoding="utf-8"))["child_order_count"] == len(schedule.child_orders)


def test_child_order_simulator_rejects_limit_up_sample(tmp_path):
    loader = _loader(tmp_path / "data")
    orders = [ExecutionOrder("20240104", "000001.SZ", "BUY", 0.1, 50_000.0)]
    parents = build_parent_orders_from_target_orders(orders)
    schedule, _capacity = build_execution_schedule(parents, loader, "20240104", ExecutionPlanConfig(buckets=("open",)))
    simulated = simulate_child_orders(schedule, loader)

    assert any(fill.status in {"FILLED", "PARTIAL", "REJECTED"} for fill in simulated.fills)
    assert all(fill.child_order_id for fill in simulated.fills)
    assert all(hasattr(fill, "commission") for fill in simulated.fills)
    assert all(fill.cost_breakdown is not None for fill in simulated.fills)


def test_close_limit_flag_does_not_block_a_non_limit_open(tmp_path):
    loader = _loader(tmp_path / "data")
    stock = loader.ts_codes.index("000001.SZ")
    date = loader.trade_dates.index("20240103")
    loader.raw_data_cache.pop("open_at_up_limit", None)
    loader.raw_data_cache["limit_up_flag"][stock, date] = 1.0
    loader.raw_data_cache["open"][stock, date] = loader.raw_data_cache["up_limit"][stock, date] - 0.5
    orders = [ExecutionOrder("20240103", "000001.SZ", "BUY", 0.1, 50_000.0)]
    schedule, _ = build_execution_schedule(
        build_parent_orders_from_target_orders(orders),
        loader,
        "20240103",
        ExecutionPlanConfig(buckets=("open",)),
    )

    simulated = simulate_child_orders(schedule, loader)

    assert simulated.fills
    assert all(fill.reason != "limit_up" for fill in simulated.fills)


def test_open_at_limit_price_blocks_buy_independently_of_close_flag(tmp_path):
    loader = _loader(tmp_path / "data")
    stock = loader.ts_codes.index("000001.SZ")
    date = loader.trade_dates.index("20240103")
    loader.raw_data_cache.pop("open_at_up_limit", None)
    loader.raw_data_cache["limit_up_flag"][stock, date] = 0.0
    loader.raw_data_cache["open"][stock, date] = loader.raw_data_cache["up_limit"][stock, date]
    orders = [ExecutionOrder("20240103", "000001.SZ", "BUY", 0.1, 50_000.0)]
    schedule, _ = build_execution_schedule(
        build_parent_orders_from_target_orders(orders),
        loader,
        "20240103",
        ExecutionPlanConfig(buckets=("open",)),
    )

    simulated = simulate_child_orders(schedule, loader)

    assert simulated.fills
    assert all(fill.status == "REJECTED" for fill in simulated.fills)
    assert {fill.reason for fill in simulated.fills} == {"limit_up"}
