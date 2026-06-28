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
        smoke_report = service.load_data_source_smoke_report()
        provider_probe = service.load_provider_probe()
        field_coverage = service.load_field_coverage_report()
        smoke_audit = service.load_data_source_audit_summary()
        incremental_recovery = service.load_incremental_recovery_report()
        baseline_compare = service.load_baseline_compare_summary()
        dataset_contracts = service.load_dataset_contracts()
        st.subheader("Data Source Smoke")
        if smoke_report:
            st.json(
                {
                    "provider": smoke_report.get("provider"),
                    "status": smoke_report.get("status"),
                    "diagnostic_counts": smoke_report.get("diagnostic_counts", {}),
                    "datasets": [
                        {
                            "dataset": item.get("dataset"),
                            "status": item.get("status"),
                            "records": item.get("records"),
                            "quality_errors": item.get("quality_errors"),
                            "quality_warnings": item.get("quality_warnings"),
                        }
                        for item in smoke_report.get("datasets", [])
                    ],
                }
            )
        else:
            st.info("No data_source_smoke_report.json found.")
        st.json(
            {
                "provider_probe_count": len(provider_probe.get("probes", [])) if provider_probe else 0,
                "field_coverage_datasets": len(field_coverage.get("datasets", [])) if field_coverage else 0,
                "audit_total_requests": smoke_audit.get("total_requests", 0) if smoke_audit else 0,
                "cache_hit_rate": smoke_audit.get("cache_hit_rate", 0.0) if smoke_audit else 0.0,
                "incremental_recovery_ok": incremental_recovery.get("ok") if incremental_recovery else None,
                "baseline_differences": baseline_compare.get("difference_count", baseline_compare.get("diff_count", 0)) if baseline_compare else 0,
                "dataset_contracts": len(dataset_contracts.get("datasets", [])) if dataset_contracts else 0,
            }
        )

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
        corpus_stats = service.load_formula_corpus_stats()
        corpus_rows = service.load_formula_corpus()
        batch_eval_result = service.load_formula_batch_eval_result()
        eval_rows = service.load_formula_eval_results()
        pretrain_result = service.load_alphagpt_pretrain_result()
        pretrain_history = service.load_alphagpt_pretrain_history()
        model_registry_report = service.load_model_registry_report()
        model_registry_manifest = service.load_model_registry_manifest()
        model_versions = service.load_model_versions()
        model_deployments = service.load_model_deployments()
        model_events = service.load_model_lifecycle_events()
        lineage_graph = service.load_model_lineage_graph()
        lifecycle_report = service.load_factor_lifecycle_report()
        health_checks = service.load_factor_health_checks()
        lifecycle_decisions = service.load_lifecycle_decisions()
        review_package = service.load_model_review_package()
        pit_report = service.load_pit_validation_report()
        pit_manifest = service.load_pit_dataset_manifest()
        pit_contracts = service.load_pit_dataset_contracts()
        lifecycle_rows = service.load_security_lifecycle()
        active_mask = service.load_active_security_mask()
        survivorship_report = service.load_survivorship_bias_report()
        leakage_report = service.load_leakage_audit_report()
        leakage_issues = service.load_leakage_issues()
        formula_leakage_scan = service.load_formula_leakage_scan()
        truncation_report = service.load_truncation_consistency_report()
        factor_value_leakage = service.load_factor_value_leakage_report()
        backtest_leakage = service.load_backtest_leakage_report()
        universe_pit_summary = service.load_universe_pit_summary()
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
        st.subheader("Formula Corpus")
        if corpus_stats:
            st.json(corpus_stats)
        else:
            st.info("No formula_corpus_stats.json found.")
        _show_dataframe_or_empty("Formula Corpus Records", corpus_rows.head(100) if not corpus_rows.empty else corpus_rows)
        st.subheader("Formula Batch Evaluation")
        if batch_eval_result:
            st.json(
                {
                    "batch_id": batch_eval_result.get("batch_id"),
                    "summary": batch_eval_result.get("summary"),
                    "benchmark": batch_eval_result.get("benchmark"),
                }
            )
        else:
            st.info("No formula_batch_eval_result.json found.")
        _show_dataframe_or_empty("Formula Eval Results", eval_rows.head(100) if not eval_rows.empty else eval_rows)
        st.subheader("AlphaGPT Pretrain")
        if pretrain_result:
            st.json(
                {
                    "status": pretrain_result.get("status"),
                    "summary": pretrain_result.get("summary"),
                    "paths": pretrain_result.get("paths"),
                }
            )
        else:
            st.info("No alphagpt_pretrain_result.json found.")
        _show_dataframe_or_empty("AlphaGPT Pretrain History", pretrain_history)
        st.subheader("Model Registry")
        st.json(
            {
                "status_counts": model_registry_report.get("status_counts", {}) if model_registry_report else {},
                "active_model_id": model_registry_report.get("active_model_id", "") if model_registry_report else "",
                "manifest_models": model_registry_manifest.get("model_count", 0) if model_registry_manifest else 0,
                "lineage_nodes": len(lineage_graph.get("nodes", [])) if lineage_graph else 0,
                "lineage_edges": len(lineage_graph.get("edges", [])) if lineage_graph else 0,
            }
        )
        _show_dataframe_or_empty("Model Versions", model_versions)
        _show_dataframe_or_empty("Model Deployments", model_deployments)
        _show_dataframe_or_empty("Model Lifecycle Events", model_events)
        st.subheader("Factor Lifecycle")
        st.json(
            {
                "recommended_action": lifecycle_report.get("decision", {}).get("recommended_action") if lifecycle_report else "",
                "decision_status": lifecycle_report.get("decision", {}).get("status") if lifecycle_report else "",
                "review_package_model": review_package.get("model_version_id", "") if review_package else "",
                "review_package_factor": review_package.get("factor_id", "") if review_package else "",
            }
        )
        _show_dataframe_or_empty("Factor Health Checks", health_checks)
        _show_dataframe_or_empty("Lifecycle Decisions", lifecycle_decisions)
        st.subheader("Point-In-Time And Leakage Governance")
        st.json(
            {
                "pit_status": pit_report.get("status", "") if pit_report else "",
                "pit_blocker_count": pit_report.get("blocker_count", 0) if pit_report else 0,
                "pit_warning_count": pit_report.get("warning_count", 0) if pit_report else 0,
                "active_universe_coverage": pit_report.get("active_universe_coverage", 0.0) if pit_report else 0.0,
                "pit_contracts": len(pit_contracts.get("datasets", [])) if pit_contracts else 0,
                "pit_manifest_datasets": len(pit_manifest.get("datasets", [])) if pit_manifest else 0,
                "security_lifecycle_rows": len(lifecycle_rows),
                "active_mask_rows": len(active_mask),
                "current_only_security_master": survivorship_report.get("current_only_security_master") if survivorship_report else None,
                "survivorship_warning_count": survivorship_report.get("warning_count", 0) if survivorship_report else 0,
                "leakage_status": leakage_report.get("status", "") if leakage_report else "",
                "leakage_gate_status": leakage_report.get("leakage_gate_status", "") if leakage_report else "",
                "leakage_blocker_count": leakage_report.get("blocker_count", 0) if leakage_report else 0,
                "leakage_warning_count": leakage_report.get("warning_count", 0) if leakage_report else 0,
                "formula_scan_blocked": formula_leakage_scan.get("blocked_formula_count", 0) if formula_leakage_scan else 0,
                "truncation_passed": truncation_report.get("passed") if truncation_report else None,
                "truncation_max_abs_diff": truncation_report.get("max_abs_diff", 0.0) if truncation_report else 0.0,
                "factor_future_date_count": factor_value_leakage.get("future_date_count", 0) if factor_value_leakage else 0,
                "backtest_leakage_gate": backtest_leakage.get("leakage_gate_status", "") if backtest_leakage else "",
                "universe_pit_summary": universe_pit_summary,
            }
        )
        _show_dataframe_or_empty("Leakage Issues", leakage_issues)
        st.subheader("Research Suite")
        if suite_result:
            st.json(
                {
                    "suite_name": suite_result.get("suite_name"),
                    "status": suite_result.get("status"),
                    "selected_factor_id": suite_result.get("selected_factor_id"),
                    "model_version_id": suite_result.get("summary", {}).get("model_version_id"),
                    "model_lifecycle_status": suite_result.get("summary", {}).get("model_lifecycle_status"),
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
        risk_exposures = service.load_risk_exposures()
        risk_decomposition = service.load_risk_decomposition()
        return_attribution = service.load_return_attribution()
        st.subheader("Risk Metrics")
        if risk_report:
            st.json(
                {
                    "metrics": risk_report.get("metrics", {}),
                    "violations": risk_report.get("violations", []),
                    "checks": risk_report.get("checks", {}),
                    "style_exposures": risk_report.get("style_exposures", {}),
                    "active_style_exposures": risk_report.get("active_style_exposures", {}),
                    "portfolio_industry": risk_report.get("portfolio", {}).get("industry_weights", {}),
                    "active_industry": risk_report.get("active", {}).get("industry_weights", {}),
                    "factor_risk_share": (risk_report.get("factor_risk_contribution") or {}).get("factor_risk_share"),
                    "specific_risk_share": (risk_report.get("factor_risk_contribution") or {}).get("specific_risk_share"),
                }
            )
        else:
            st.info("No risk_report.json found.")
        st.subheader("Optimization Result")
        st.json(optimization if optimization else {"status": "No optimization_result.json found"})
        _show_dataframe_or_empty("Daily Style And Active Exposures", risk_exposures)
        _show_dataframe_or_empty("Risk Decomposition", risk_decomposition)
        _show_dataframe_or_empty("Return Attribution", return_attribution)
        if risk_markdown:
            st.markdown(risk_markdown)

    with orders_tab:
        targets = service.load_target_positions()
        orders = service.load_orders()
        fills = service.load_paper_fills()
        capacity_report = service.load_capacity_report()
        execution_plan = service.load_execution_plan()
        parent_orders = service.load_parent_orders()
        child_orders = service.load_child_orders()
        child_fills = service.load_child_fills()
        execution_quality = service.load_execution_quality()
        broker_report = service.load_broker_report()
        broker_reconciliation = service.load_broker_reconciliation()
        broker_orders = service.load_broker_orders()
        broker_events = service.load_broker_events()
        broker_fills = service.load_broker_fills()
        broker_manifest = service.load_broker_instruction_manifest()
        st.plotly_chart(plot_order_distribution(orders), use_container_width=True)
        st.subheader("Capacity And Execution Quality")
        st.json(
            {
                "capacity": (capacity_report.get("portfolio") or {}) if capacity_report else {},
                "execution_quality": execution_quality,
                "child_orders": len(child_orders),
                "child_fills": len(child_fills),
                "buckets": (execution_plan.get("schedule") or {}).get("buckets", []) if execution_plan else [],
            }
        )
        st.subheader("Broker Adapter")
        broker_status_counts = broker_orders["status"].value_counts().to_dict() if not broker_orders.empty and "status" in broker_orders else {}
        st.json(
            {
                "summary": broker_report.get("summary", {}) if broker_report else {},
                "status_distribution": broker_status_counts,
                "reconciliation": broker_reconciliation,
                "file_manifest": broker_manifest,
            }
        )
        _show_dataframe_or_empty("Target Positions", targets)
        _show_dataframe_or_empty("Orders", orders)
        _show_dataframe_or_empty("Parent Orders", parent_orders)
        _show_dataframe_or_empty("Child Orders", child_orders)
        _show_dataframe_or_empty("Paper Fills", fills)
        _show_dataframe_or_empty("Child Fills", child_fills)
        _show_dataframe_or_empty("Broker Orders", broker_orders)
        _show_dataframe_or_empty("Broker Fills", broker_fills)
        _show_dataframe_or_empty("Broker Events", broker_events)

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
        broker_report = service.load_broker_report()
        broker_reconciliation = service.load_broker_reconciliation()
        model_registry_report = service.load_model_registry_report()
        lifecycle_report = service.load_factor_lifecycle_report()
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
        st.subheader("Broker")
        st.json(
            {
                "broker_summary": broker_report.get("summary", {}) if broker_report else {},
                "reconciliation": broker_reconciliation,
            }
        )
        st.subheader("Model Lifecycle")
        st.json(
            {
                "registry_status_counts": model_registry_report.get("status_counts", {}) if model_registry_report else {},
                "active_model_id": model_registry_report.get("active_model_id", "") if model_registry_report else "",
                "lifecycle_recommended_action": lifecycle_report.get("decision", {}).get("recommended_action") if lifecycle_report else "",
                "lifecycle_status": lifecycle_report.get("decision", {}).get("status") if lifecycle_report else "",
            }
        )
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
        artifact_validation = service.load_artifact_validation_report()
        artifact_manifest = service.load_artifact_schema_manifest()
        release_gate = service.load_release_gate_report()
        release_manifest = service.load_release_manifest()
        dependency_inventory = service.load_dependency_inventory()
        module_inventory = service.load_module_inventory()
        cli_inventory = service.load_cli_inventory()
        ci_report = service.load_ci_report()

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

        st.subheader("Release And Artifact Schema")
        st.json(
            {
                "schema_errors": artifact_validation.get("error_count", 0) if artifact_validation else 0,
                "schema_warnings": artifact_validation.get("warning_count", 0) if artifact_validation else 0,
                "legacy_artifacts": artifact_validation.get("legacy_artifact_count", 0) if artifact_validation else 0,
                "schema_manifest_entries": len(artifact_manifest.get("entries", [])) if artifact_manifest else 0,
                "release_gate_status": release_gate.get("status", "") if release_gate else "",
                "release_gate_errors": release_gate.get("error_count", 0) if release_gate else 0,
                "build_artifacts": len(release_manifest.get("build_artifacts", [])) if release_manifest else 0,
                "dependency_files": len(dependency_inventory.get("files", [])) if dependency_inventory else 0,
                "platform_modules": len(module_inventory.get("modules", [])) if module_inventory else 0,
                "cli_entries": len(cli_inventory.get("entries", [])) if cli_inventory else 0,
                "ci_status": ci_report.get("status", "") if ci_report else "",
                "ci_commands": len(ci_report.get("commands", [])) if ci_report else 0,
            }
        )


def main() -> None:
    render_app()


if __name__ == "__main__":
    main()
