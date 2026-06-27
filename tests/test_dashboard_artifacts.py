import importlib

import plotly.graph_objects as go

from backtest import run_backtest
from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from dashboard.visualizer import (
    plot_backtest_metrics,
    plot_equity_curve,
    plot_factor_split_metrics,
    plot_order_distribution,
)
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from model_core import engine
from strategy_manager import runner


def prepare_dashboard_artifacts(tmp_path):
    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    report_dir = tmp_path / "reports"
    backtest_dir = tmp_path / "backtest"
    orders_dir = tmp_path / "orders"

    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()
    engine.main(
        [
            "--dry-run",
            "--register",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--factor-store-dir",
            str(store_dir),
            "--report-dir",
            str(report_dir),
        ]
    )
    run_backtest.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(backtest_dir),
            "--top-n",
            "2",
            "--max-weight",
            "0.10",
        ]
    )
    runner.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(orders_dir),
            "--top-n",
            "2",
            "--max-weight",
            "0.10",
        ]
    )
    return DashboardConfig(
        data_dir=data_dir,
        factor_store_dir=store_dir,
        report_dir=report_dir,
        backtest_dir=backtest_dir,
        orders_dir=orders_dir,
    )


def test_dashboard_service_reads_local_artifacts(tmp_path, capsys):
    config = prepare_dashboard_artifacts(tmp_path)
    capsys.readouterr()
    service = AshareDashboardService(config)

    assert service.load_manifest()["provider"] == "sample"
    assert not service.load_dataset("securities").empty
    assert not service.load_dataset("daily_bars").empty
    assert not service.load_factors().empty
    assert not service.load_experiments().empty
    assert service.load_factor_report_json()["factor_id"].startswith("factor_")
    assert service.load_backtest_result()["metrics"]
    assert not service.load_equity_curve().empty
    assert not service.load_orders().empty
    assert not service.load_paper_fills().empty


def test_dashboard_visualizers_return_plotly_figures(tmp_path, capsys):
    config = prepare_dashboard_artifacts(tmp_path)
    capsys.readouterr()
    service = AshareDashboardService(config)

    assert isinstance(plot_equity_curve(service.load_equity_curve()), go.Figure)
    assert isinstance(plot_backtest_metrics(service.load_backtest_result()["metrics"]), go.Figure)
    assert isinstance(
        plot_factor_split_metrics(service.load_factor_report_json()["metrics_by_split"]),
        go.Figure,
    )
    assert isinstance(plot_order_distribution(service.load_orders()), go.Figure)


def test_dashboard_app_import_has_no_external_side_effects():
    module = importlib.import_module("dashboard.app")

    assert hasattr(module, "render_app")
