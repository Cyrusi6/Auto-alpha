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
                ]
            ),
            language="text",
        )
        if st.button("Refresh"):
            st.rerun()

    data_tab, factor_tab, report_tab, backtest_tab, orders_tab = st.tabs(
        ["Data", "Factors", "Reports", "Backtest", "Orders"]
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

    with orders_tab:
        targets = service.load_target_positions()
        orders = service.load_orders()
        fills = service.load_paper_fills()
        st.plotly_chart(plot_order_distribution(orders), use_container_width=True)
        _show_dataframe_or_empty("Target Positions", targets)
        _show_dataframe_or_empty("Orders", orders)
        _show_dataframe_or_empty("Paper Fills", fills)


def main() -> None:
    render_app()


if __name__ == "__main__":
    main()
