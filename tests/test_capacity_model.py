import json

from capacity_model import (
    CapacityConfig,
    estimate_impact_cost,
    estimate_portfolio_capacity,
    estimate_security_capacity,
    write_capacity_report,
    build_capacity_report,
)
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from execution import ExecutionOrder
from model_core.data_loader import AShareDataLoader


def _loader(tmp_path):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=tmp_path)).sync()
    return AShareDataLoader(data_dir=tmp_path, device="cpu").load_data()


def test_security_and_portfolio_capacity_report(tmp_path):
    loader = _loader(tmp_path / "data")
    order = ExecutionOrder("20240104", "000001.SZ", "BUY", 0.1, 50_000.0)

    capacity = estimate_security_capacity(loader, "000001.SZ", "20240104", order_value=order.order_value)
    portfolio = estimate_portfolio_capacity(loader, [order], "20240104", CapacityConfig(max_participation=0.10))
    report = build_capacity_report(portfolio, CapacityConfig(max_participation=0.10))
    json_path, md_path = write_capacity_report(report, tmp_path / "capacity")

    assert capacity.max_trade_value >= 0
    assert capacity.max_trade_shares >= 0
    assert portfolio.estimated_impact_cost >= 0
    assert json.loads(json_path.read_text(encoding="utf-8"))["portfolio"]["records"][0]["ts_code"] == "000001.SZ"
    assert "Capacity Report" in md_path.read_text(encoding="utf-8")


def test_impact_cost_increases_with_order_value():
    small = estimate_impact_cost(10_000.0, 1_000_000.0, 0.02, "BUY")
    large = estimate_impact_cost(100_000.0, 1_000_000.0, 0.02, "BUY")

    assert large > small >= 0
