from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService


def test_dashboard_service_reads_matrix_benchmark_and_cross_source_artifacts(tmp_path):
    data_dir = tmp_path / "data"
    matrix_dir = data_dir / "matrix_cache"
    benchmark_dir = tmp_path / "benchmark"
    cross_dir = tmp_path / "cross_source"
    matrix_dir.mkdir(parents=True)
    benchmark_dir.mkdir(parents=True)
    cross_dir.mkdir(parents=True)
    (matrix_dir / "metadata.json").write_text(
        '{"n_stocks":3,"n_dates":3,"fields":["close"],"cache_hash":"abc"}',
        encoding="utf-8",
    )
    (matrix_dir / "matrix_validation_report.json").write_text(
        '{"valid":true,"errors":[],"warnings":[]}',
        encoding="utf-8",
    )
    (benchmark_dir / "benchmark_result.json").write_text(
        '{"summary":{"successful_items":2},"items":[{"name":"jsonl_loader_load_data"}]}',
        encoding="utf-8",
    )
    (benchmark_dir / "benchmark_report.md").write_text("# Performance Benchmark Report", encoding="utf-8")
    (cross_dir / "cross_source_report.json").write_text(
        '{"has_differences":false,"datasets":[]}',
        encoding="utf-8",
    )
    (cross_dir / "cross_source_report.md").write_text("# Cross Source Check Report", encoding="utf-8")

    service = AshareDashboardService(
        DashboardConfig(
            data_dir=data_dir,
            factor_store_dir=tmp_path / "store",
            report_dir=tmp_path / "reports",
            backtest_dir=tmp_path / "backtest",
            orders_dir=tmp_path / "orders",
            matrix_cache_dir=matrix_dir,
            benchmark_dir=benchmark_dir,
            cross_source_dir=cross_dir,
        )
    )

    assert service.load_matrix_metadata()["cache_hash"] == "abc"
    assert service.load_matrix_validation_report()["valid"] is True
    assert service.load_benchmark_result()["summary"]["successful_items"] == 2
    assert "Performance Benchmark" in service.load_benchmark_report_markdown()
    assert service.load_cross_source_report()["has_differences"] is False
    assert "Cross Source" in service.load_cross_source_report_markdown()
