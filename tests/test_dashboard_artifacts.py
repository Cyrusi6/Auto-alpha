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
    assert not service.load_dataset("daily_limits").empty
    assert not service.load_dataset("adjustment_factors").empty
    assert not service.load_dataset("index_members").empty
    assert not service.load_factors().empty
    overview = service.load_factor_overview()
    assert not overview.empty
    assert {
        "status",
        "transform_method",
        "gate_status",
        "max_abs_correlation",
        "similar_factors",
        "factor_type",
        "batch_id",
        "component_factor_ids",
        "formula_complexity",
        "formula_lookback",
        "formula_source",
        "generation",
    } <= set(overview.columns)
    assert not service.load_experiments().empty
    assert service.load_factor_report_json()["factor_id"].startswith("factor_")
    assert service.load_backtest_result()["metrics"]
    assert "fill_rate" in service.load_backtest_result()["metrics"]
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


def test_dashboard_service_reads_batch_report(tmp_path):
    report_dir = tmp_path / "reports"
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir(parents=True)
    (batch_dir / "batch_report.json").write_text(
        '{"batch_id":"batch_test","summary":{"total_candidates":1}}',
        encoding="utf-8",
    )
    (batch_dir / "batch_report.md").write_text("# Batch Research Report", encoding="utf-8")
    service = AshareDashboardService(
        DashboardConfig(
            data_dir=tmp_path / "data",
            factor_store_dir=tmp_path / "store",
            report_dir=report_dir,
            backtest_dir=tmp_path / "backtest",
            orders_dir=tmp_path / "orders",
        )
    )

    assert service.load_batch_report_json()["batch_id"] == "batch_test"
    assert "Batch Research" in service.load_batch_report_markdown()


def test_dashboard_service_reads_search_report(tmp_path):
    report_dir = tmp_path / "reports"
    search_dir = tmp_path / "search"
    search_dir.mkdir(parents=True)
    (search_dir / "search_report.json").write_text(
        '{"search_id":"search_test","candidates_evaluated":3}',
        encoding="utf-8",
    )
    (search_dir / "search_report.md").write_text("# Formula Search Report", encoding="utf-8")
    service = AshareDashboardService(
        DashboardConfig(
            data_dir=tmp_path / "data",
            factor_store_dir=tmp_path / "store",
            report_dir=report_dir,
            backtest_dir=tmp_path / "backtest",
            orders_dir=tmp_path / "orders",
        )
    )

    assert service.load_search_report_json()["search_id"] == "search_test"
    assert "Formula Search" in service.load_search_report_markdown()


def test_dashboard_service_reads_suite_artifacts(tmp_path):
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir(parents=True)
    (suite_dir / "suite_result.json").write_text(
        '{"suite_name":"sample_suite","status":"success","stages":[]}',
        encoding="utf-8",
    )
    (suite_dir / "suite_report.md").write_text("# Research Suite Report", encoding="utf-8")
    (suite_dir / "artifact_catalog.json").write_text(
        '{"suite_name":"sample_suite","created_at":"now","entries":[]}',
        encoding="utf-8",
    )
    (suite_dir / "promotion_decision.json").write_text(
        '{"factor_id":"factor_x","passed":true}',
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

    assert service.load_suite_result()["suite_name"] == "sample_suite"
    assert "Research Suite" in service.load_suite_report_markdown()
    assert service.load_artifact_catalog()["suite_name"] == "sample_suite"
    assert service.load_promotion_decision()["passed"] is True


def test_dashboard_service_reads_production_sync_artifacts(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sync_plan.json").write_text(
        '{"plan_id":"plan_test","jobs":[{"job_id":"job_1"}]}',
        encoding="utf-8",
    )
    (data_dir / "pipeline_state.json").write_text(
        '{"updated_at":"now","datasets":{}}',
        encoding="utf-8",
    )
    (data_dir / "api_audit.jsonl").write_text(
        '{"api_name":"daily","dataset":"daily_bars","status":"success","cache_hit":false}\n',
        encoding="utf-8",
    )
    (data_dir / "dataset_stats.json").write_text(
        '{"datasets":[{"dataset":"daily_bars","records":6}]}',
        encoding="utf-8",
    )
    snapshot_dataset = data_dir / "snapshots" / "snap_test" / "daily_bars"
    snapshot_dataset.mkdir(parents=True)
    (snapshot_dataset / "records.jsonl").write_text("{}", encoding="utf-8")
    service = AshareDashboardService(
        DashboardConfig(
            data_dir=data_dir,
            factor_store_dir=tmp_path / "store",
            report_dir=tmp_path / "reports",
            backtest_dir=tmp_path / "backtest",
            orders_dir=tmp_path / "orders",
        )
    )

    assert service.load_sync_plan()["plan_id"] == "plan_test"
    assert service.load_pipeline_state()["updated_at"] == "now"
    assert not service.load_api_audit().empty
    assert service.load_dataset_stats()["datasets"][0]["records"] == 6
    snapshots = service.load_snapshot_summary()
    assert snapshots.iloc[0]["snapshot"] == "snap_test"


def test_dashboard_app_import_has_no_external_side_effects():
    module = importlib.import_module("dashboard.app")

    assert hasattr(module, "render_app")
