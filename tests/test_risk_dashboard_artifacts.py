from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService


def test_dashboard_service_reads_risk_artifacts(tmp_path):
    backtest_risk = tmp_path / "backtest" / "risk"
    backtest_risk.mkdir(parents=True)
    (backtest_risk / "risk_report.json").write_text(
        '{"metrics":{"tracking_error":0.1},"violations":[]}',
        encoding="utf-8",
    )
    (backtest_risk / "risk_report.md").write_text("# Risk Report", encoding="utf-8")
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
    assert "Risk Report" in service.load_risk_report_markdown()
    assert service.load_optimization_result()["factor_id"] == "factor_x"
