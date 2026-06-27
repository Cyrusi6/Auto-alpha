from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService


def test_dashboard_service_reads_risk_artifacts(tmp_path):
    backtest_risk = tmp_path / "backtest" / "risk"
    backtest_risk.mkdir(parents=True)
    (backtest_risk / "risk_model_report.json").write_text(
        '{"metrics":{"tracking_error":0.1},"violations":[],"style_exposures":{"size":0.1}}',
        encoding="utf-8",
    )
    (backtest_risk / "risk_model_report.md").write_text("# Risk Model Report", encoding="utf-8")
    (tmp_path / "backtest" / "risk_exposures.jsonl").write_text(
        '{"trade_date":"20240104","max_active_style_exposure_abs":0.2}\n',
        encoding="utf-8",
    )
    (tmp_path / "backtest" / "risk_decomposition.jsonl").write_text(
        '{"trade_date":"20240104","active":{"total_risk":0.1}}\n',
        encoding="utf-8",
    )
    (tmp_path / "backtest" / "return_attribution.jsonl").write_text(
        '{"trade_date":"20240104","total_active_return":0.01}\n',
        encoding="utf-8",
    )
    orders_plan = tmp_path / "orders" / "plan"
    orders_plan.mkdir(parents=True)
    (orders_plan / "capacity_report.json").write_text(
        '{"portfolio":{"capacity_warning_count":1,"estimated_impact_cost":10.0}}',
        encoding="utf-8",
    )
    (orders_plan / "execution_plan.json").write_text(
        '{"schedule":{"buckets":["open"],"child_orders":[]},"quality":{"execution_fill_rate":0.5}}',
        encoding="utf-8",
    )
    (orders_plan / "parent_orders.jsonl").write_text('{"parent_order_id":"parent_1"}\n', encoding="utf-8")
    (orders_plan / "child_orders.jsonl").write_text('{"child_order_id":"child_1"}\n', encoding="utf-8")
    (orders_plan / "child_fills.jsonl").write_text('{"child_order_id":"child_1","status":"FILLED"}\n', encoding="utf-8")
    (orders_plan / "execution_quality.json").write_text('{"execution_fill_rate":0.5}\n', encoding="utf-8")
    (tmp_path / "backtest" / "optimization_result.json").write_text(
        '{"factor_id":"factor_x","weights":{"000001.SZ":0.1}}',
        encoding="utf-8",
    )
    service = AshareDashboardService(
        DashboardConfig(
            data_dir=tmp_path / "data",
            factor_store_dir=tmp_path / "store",
            report_dir=tmp_path / "reports",
            backtest_dir=tmp_path / "backtest",
            orders_dir=tmp_path / "orders",
        )
    )

    assert service.load_risk_report_json()["metrics"]["tracking_error"] == 0.1
    assert "Risk Model Report" in service.load_risk_report_markdown()
    assert service.load_optimization_result()["factor_id"] == "factor_x"
    assert not service.load_risk_exposures().empty
    assert not service.load_risk_decomposition().empty
    assert not service.load_return_attribution().empty
    assert service.load_capacity_report()["portfolio"]["capacity_warning_count"] == 1
    assert service.load_execution_quality()["execution_fill_rate"] == 0.5
    assert not service.load_parent_orders().empty
    assert not service.load_child_orders().empty
    assert not service.load_child_fills().empty
