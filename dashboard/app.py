"""Streamlit entry point for the A-share factor research dashboard."""

from __future__ import annotations

import json

import streamlit as st

try:
    from .config import DashboardConfig
    from .data_service import AshareDashboardService
    from .visualizer import (
        plot_backtest_metrics,
        plot_equity_curve,
        plot_factor_split_metrics,
        plot_order_distribution,
    )
except ImportError:  # pragma: no cover - streamlit script execution
    from config import DashboardConfig
    from data_service import AshareDashboardService
    from visualizer import (
        plot_backtest_metrics,
        plot_equity_curve,
        plot_factor_split_metrics,
        plot_order_distribution,
    )


def _show_dataframe_or_empty(title: str, frame) -> None:
    st.subheader(title)
    if frame.empty:
        st.info("No local artifact found.")
    else:
        st.dataframe(frame, use_container_width=True, hide_index=True)


def render_app(config: DashboardConfig | None = None) -> None:
    st.set_page_config(page_title="A-Share Factor Research", layout="wide")
    service = AshareDashboardService(config)

    st.title("A-Share Factor Research Platform")
    st.caption("Local artifacts only: data, factor store, reports, portfolio simulation, and paper orders.")

    with st.sidebar:
        st.header("Artifact Paths")
        st.code(
            "\n".join(
                [
                    f"data_dir={service.config.data_dir}",
                    f"factor_store_dir={service.config.factor_store_dir}",
                    f"report_dir={service.config.report_dir}",
                    f"backtest_dir={service.config.backtest_dir}",
                    f"orders_dir={service.config.orders_dir}",
                    f"matrix_cache_dir={service.config.matrix_cache_dir}",
                    f"benchmark_dir={service.config.benchmark_dir}",
                ]
            ),
            language="text",
        )
        if st.button("Refresh"):
            st.rerun()

    data_tab, factor_tab, report_tab, backtest_tab, risk_tab, orders_tab, production_tab, performance_tab = st.tabs(
        ["Data", "Factors", "Reports", "Backtest", "Risk", "Orders", "Production", "Performance"]
    )

    with data_tab:
        manifest = service.load_manifest()
        st.subheader("Manifest")
        st.json(manifest if manifest else {"status": "No manifest found"})
        quality_report = service.load_quality_report()
        st.subheader("Quality Summary")
        if quality_report:
            st.json(
                {
                    "has_errors": quality_report.get("has_errors", False),
                    "total_errors": quality_report.get("total_errors", 0),
                    "total_warnings": quality_report.get("total_warnings", 0),
                    "datasets": [
                        {
                            "dataset": dataset.get("dataset"),
                            "records": dataset.get("records"),
                            "errors": dataset.get("errors"),
                            "warnings": dataset.get("warnings"),
                        }
                        for dataset in quality_report.get("datasets", [])
                    ],
                }
            )
        else:
            st.info("No quality_report.json found.")
        sync_plan = service.load_sync_plan()
        pipeline_state = service.load_pipeline_state()
        dataset_stats = service.load_dataset_stats()
        st.subheader("Production Sync")
        st.json(
            {
                "plan_id": sync_plan.get("plan_id"),
                "jobs": len(sync_plan.get("jobs", [])),
                "state_updated_at": pipeline_state.get("updated_at"),
                "dataset_stats": len(dataset_stats.get("datasets", [])),
            }
        )
        _show_dataframe_or_empty("API Request Audit", service.load_api_audit())
        _show_dataframe_or_empty("Snapshots", service.load_snapshot_summary())
        col1, col2 = st.columns(2)
        with col1:
            _show_dataframe_or_empty("Securities", service.load_dataset("securities"))
            _show_dataframe_or_empty("Daily Limits", service.load_dataset("daily_limits"))
            _show_dataframe_or_empty("Index Members", service.load_dataset("index_members"))
        with col2:
            _show_dataframe_or_empty("Daily Bars", service.load_dataset("daily_bars"))
            _show_dataframe_or_empty("Adjustment Factors", service.load_dataset("adjustment_factors"))

    with factor_tab:
        factors = service.load_factors()
        factor_overview = service.load_factor_overview()
        experiments = service.load_experiments()
        _show_dataframe_or_empty("Factor Gate And Transform Overview", factor_overview)
        _show_dataframe_or_empty("Factors", factors)
        _show_dataframe_or_empty("Experiments", experiments)
        latest_metrics = service.load_latest_factor_metrics()
        st.subheader("Latest Factor Metrics")
        st.json(latest_metrics if latest_metrics else {"status": "No factor metrics found"})

    with report_tab:
        report = service.load_factor_report_json()
        markdown = service.load_factor_report_markdown()
        batch_report = service.load_batch_report_json()
        batch_markdown = service.load_batch_report_markdown()
        search_report = service.load_search_report_json()
        search_markdown = service.load_search_report_markdown()
        neural_result = service.load_neural_search_result()
        neural_history = service.load_neural_training_history()
        neural_markdown = service.load_neural_search_report_markdown()
        neural_checkpoints = service.load_neural_checkpoints()
        suite_result = service.load_suite_result()
        suite_markdown = service.load_suite_report_markdown()
        artifact_catalog = service.load_artifact_catalog()
        promotion_decision = service.load_promotion_decision()
        if report:
            st.subheader("Factor Report")
            st.plotly_chart(plot_factor_split_metrics(report.get("metrics_by_split", {})), use_container_width=True)
            st.json(report)
        else:
            st.info("No factor_report.json found.")
        if markdown:
            st.markdown(markdown)
        st.subheader("Batch Research")
        if batch_report:
            st.json(
                {
                    "batch_id": batch_report.get("batch_id"),
                    "created_at": batch_report.get("created_at"),
                    "composite_factor_id": batch_report.get("composite_factor_id"),
                    "summary": batch_report.get("summary"),
                }
            )
        else:
            st.info("No batch_report.json found.")
        if batch_markdown:
            st.markdown(batch_markdown)
        st.subheader("Formula Search")
        if search_report:
            st.json(
                {
                    "search_id": search_report.get("search_id"),
                    "composite_factor_id": search_report.get("composite_factor_id"),
                    "candidates_generated": search_report.get("candidates_generated"),
                    "candidates_evaluated": search_report.get("candidates_evaluated"),
                    "generations": search_report.get("generations"),
                }
            )
        else:
            st.info("No search_report.json found.")
        if search_markdown:
            st.markdown(search_markdown)
        st.subheader("Neural Search")
        if neural_result:
            st.json(
                {
                    "search_id": neural_result.get("search_id"),
                    "candidates_evaluated": neural_result.get("candidates_evaluated"),
                    "approved_factor_ids": neural_result.get("approved_factor_ids", []),
                    "composite_factor_id": neural_result.get("composite_factor_id"),
                    "checkpoint_paths": neural_result.get("checkpoint_paths", []),
                    "best_formulas": neural_result.get("best_formulas", [])[:5],
                }
            )
        else:
            st.info("No neural_search_result.json found.")
        _show_dataframe_or_empty("Neural Training History", neural_history)
        _show_dataframe_or_empty("Neural Checkpoints", neural_checkpoints)
        if neural_markdown:
            st.markdown(neural_markdown)
        st.subheader("Research Suite")
        if suite_result:
            st.json(
                {
                    "suite_name": suite_result.get("suite_name"),
                    "status": suite_result.get("status"),
                    "selected_factor_id": suite_result.get("selected_factor_id"),
                    "stages": [
                        {
                            "name": stage.get("name"),
                            "status": stage.get("status"),
                            "error": stage.get("error"),
                        }
                        for stage in suite_result.get("stages", [])
                    ],
                }
            )
        else:
            st.info("No suite_result.json found.")
        if promotion_decision:
            st.subheader("Promotion Decision")
            st.json(promotion_decision)
        if artifact_catalog:
            st.subheader("Artifact Catalog")
            st.json(
                {
                    "suite_name": artifact_catalog.get("suite_name"),
                    "entries": len(artifact_catalog.get("entries", [])),
                }
            )
        if suite_markdown:
            st.markdown(suite_markdown)

    with backtest_tab:
        result = service.load_backtest_result()
        equity_curve = service.load_equity_curve()
        trades = service.load_trades()
        metrics = result.get("metrics", {}) if result else {}
        st.plotly_chart(plot_backtest_metrics(metrics), use_container_width=True)
        st.plotly_chart(plot_equity_curve(equity_curve), use_container_width=True)
        _show_dataframe_or_empty("Trades", trades)

    with risk_tab:
        risk_report = service.load_risk_report_json()
        risk_markdown = service.load_risk_report_markdown()
        optimization = service.load_optimization_result()
        st.subheader("Risk Metrics")
        if risk_report:
            st.json(
                {
                    "metrics": risk_report.get("metrics", {}),
                    "violations": risk_report.get("violations", []),
                    "checks": risk_report.get("checks", {}),
                    "portfolio_industry": risk_report.get("portfolio", {}).get("industry_weights", {}),
                    "active_industry": risk_report.get("active", {}).get("industry_weights", {}),
                }
            )
        else:
            st.info("No risk_report.json found.")
        st.subheader("Optimization Result")
        st.json(optimization if optimization else {"status": "No optimization_result.json found"})
        if risk_markdown:
            st.markdown(risk_markdown)

    with orders_tab:
        targets = service.load_target_positions()
        orders = service.load_orders()
        fills = service.load_paper_fills()
        st.plotly_chart(plot_order_distribution(orders), use_container_width=True)
        _show_dataframe_or_empty("Target Positions", targets)
        _show_dataframe_or_empty("Orders", orders)
        _show_dataframe_or_empty("Paper Fills", fills)

    with production_tab:
        production_run = service.load_production_run()
        production_markdown = service.load_production_run_markdown()
        approvals = service.load_approvals()
        approval_log = service.load_approval_log()
        account_state = service.load_paper_account_state()
        positions = service.load_paper_positions()
        snapshots = service.load_account_snapshots()
        trade_ledger = service.load_trade_ledger()
        monitoring_report = service.load_monitoring_report()
        monitoring_markdown = service.load_monitoring_report_markdown()
        monitoring_alerts = service.load_monitoring_alerts()
        st.subheader("Production Run")
        st.json(
            production_run
            if production_run
            else {"status": "No production_run.json found"}
        )
        if production_markdown:
            st.markdown(production_markdown)
        st.subheader("Approvals")
        _show_dataframe_or_empty("Approval Batches", approvals)
        _show_dataframe_or_empty("Approval Log", approval_log)
        st.subheader("Paper Account")
        st.json(
            {
                "account_id": account_state.get("account_id"),
                "cash": account_state.get("cash"),
                "positions": len(account_state.get("positions", {})) if account_state else 0,
                "updated_at": account_state.get("updated_at"),
            }
            if account_state
            else {"status": "No account_state.json found"}
        )
        _show_dataframe_or_empty("Paper Positions", positions)
        _show_dataframe_or_empty("Account Snapshots", snapshots)
        _show_dataframe_or_empty("Trade Ledger", trade_ledger)
        st.subheader("Monitoring")
        st.json(
            {
                "as_of_date": monitoring_report.get("as_of_date"),
                "alerts": len(monitoring_report.get("alerts", [])),
                "checks": list((monitoring_report.get("checks") or {}).keys()),
            }
            if monitoring_report
            else {"status": "No monitoring_report.json found"}
        )
        _show_dataframe_or_empty("Monitoring Alerts", monitoring_alerts)
        if monitoring_markdown:
            st.markdown(monitoring_markdown)

    with performance_tab:
        matrix_metadata = service.load_matrix_metadata()
        matrix_validation = service.load_matrix_validation_report()
        benchmark_result = service.load_benchmark_result()
        benchmark_markdown = service.load_benchmark_report_markdown()
        cross_source_report = service.load_cross_source_report()
        cross_source_markdown = service.load_cross_source_report_markdown()

        st.subheader("Matrix Cache")
        if matrix_metadata:
            st.json(
                {
                    "n_stocks": matrix_metadata.get("n_stocks"),
                    "n_dates": matrix_metadata.get("n_dates"),
                    "fields": len(matrix_metadata.get("fields", [])),
                    "cache_hash": matrix_metadata.get("cache_hash"),
                    "universe_name": matrix_metadata.get("universe_name"),
                }
            )
        else:
            st.info("No matrix cache metadata found.")
        if matrix_validation:
            st.subheader("Matrix Validation")
            st.json(
                {
                    "valid": matrix_validation.get("valid"),
                    "errors": matrix_validation.get("errors", []),
                    "warnings": matrix_validation.get("warnings", []),
                }
            )

        st.subheader("Performance Benchmark")
        if benchmark_result:
            st.json(
                {
                    "summary": benchmark_result.get("summary", {}),
                    "items": [
                        {
                            "name": item.get("name"),
                            "seconds": item.get("wall_time_seconds"),
                            "success": item.get("success"),
                            "error": item.get("error"),
                        }
                        for item in benchmark_result.get("items", [])
                    ],
                }
            )
        else:
            st.info("No benchmark_result.json found.")
        if benchmark_markdown:
            st.markdown(benchmark_markdown)

        st.subheader("Cross Source Checks")
        if cross_source_report:
            st.json(
                {
                    "has_differences": cross_source_report.get("has_differences"),
                    "datasets": [
                        {
                            "dataset": item.get("dataset"),
                            "record_count_diff": item.get("record_count_diff"),
                            "missing_keys_left": item.get("missing_keys_left"),
                            "missing_keys_right": item.get("missing_keys_right"),
                            "numeric_field_max_abs_diff": item.get("numeric_field_max_abs_diff"),
                        }
                        for item in cross_source_report.get("datasets", [])
                    ],
                }
            )
        else:
            st.info("No cross_source_report.json found.")
        if cross_source_markdown:
            st.markdown(cross_source_markdown)


def main() -> None:
    render_app()


if __name__ == "__main__":
    main()
