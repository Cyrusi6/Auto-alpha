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
        raw_index_manifest = service.load_raw_data_index_manifest()
        raw_index_report = service.load_raw_data_index_report()
        raw_index_validation = service.load_raw_data_index_validation_report()
        st.subheader("Raw Data Index")
        if raw_index_manifest or raw_index_report:
            summary = raw_index_report.get("summary", {}) if raw_index_report else {}
            st.json(
                {
                    "status": raw_index_manifest.get("status") or raw_index_report.get("status"),
                    "dataset_count": raw_index_manifest.get("dataset_count") or summary.get("raw_data_index_dataset_count", 0),
                    "total_records": raw_index_manifest.get("total_records") or summary.get("raw_data_index_record_count", 0),
                    "total_size_gb": summary.get("raw_data_index_size_gb", 0.0),
                    "partition_count": raw_index_manifest.get("partition_count") or summary.get("raw_data_index_partition_count", 0),
                    "parse_errors": raw_index_manifest.get("total_parse_errors") or summary.get("raw_data_index_parse_error_count", 0),
                    "validation_status": raw_index_validation.get("status"),
                    "stale_datasets": raw_index_validation.get("stale_dataset_count", 0) if raw_index_validation else 0,
                    "index_hash": raw_index_manifest.get("index_hash"),
                }
            )
        else:
            st.info("No raw_data_index_manifest.json found.")
        _show_dataframe_or_empty("Raw Dataset Indexes", service.load_raw_dataset_indexes())
        _show_dataframe_or_empty("Raw Partition Summary", service.load_raw_partitions())
        _show_dataframe_or_empty("Raw Data Index Issues", service.load_raw_data_index_issues())
        data_quality_report = service.load_data_quality_lab_report()
        data_quality_scorecard = service.load_data_quality_scorecard()
        data_quality_freeze_gate = service.load_data_quality_freeze_gate()
        st.subheader("Semantic Data Quality")
        if data_quality_report or data_quality_scorecard or data_quality_freeze_gate:
            st.json(
                {
                    "status": data_quality_freeze_gate.get("status")
                    or data_quality_scorecard.get("status")
                    or data_quality_report.get("status"),
                    "issue_count": data_quality_scorecard.get("issue_count", 0),
                    "errors": data_quality_scorecard.get("error_count", 0),
                    "warnings": data_quality_scorecard.get("warning_count", 0),
                    "can_create_freeze": data_quality_freeze_gate.get("can_create_freeze"),
                    "can_build_matrix": data_quality_freeze_gate.get("can_build_matrix"),
                    "can_run_core_alpha": data_quality_freeze_gate.get("can_run_core_alpha"),
                    "can_run_expanded_alpha": data_quality_freeze_gate.get("can_run_expanded_alpha"),
                    "recommended_next_action": data_quality_freeze_gate.get("recommended_next_action"),
                }
            )
        else:
            st.info("No data_quality_lab_report.json found.")
        _show_dataframe_or_empty("Dataset Semantic Quality", service.load_dataset_quality_summary())
        _show_dataframe_or_empty("Semantic Quality Issues", service.load_data_quality_issues())
        _show_dataframe_or_empty("Semantic Repair Suggestions", service.load_data_quality_repair_suggestions())
        _show_dataframe_or_empty("API Request Audit", service.load_api_audit())
        _show_dataframe_or_empty("Snapshots", service.load_snapshot_summary())
        col1, col2 = st.columns(2)
        with col1:
            _show_dataframe_or_empty("Securities", service.load_dataset("securities"))
            _show_dataframe_or_empty("Daily Limits", service.load_dataset("daily_limits"))
            _show_dataframe_or_empty("Index Members", service.load_dataset("index_members"))
            _show_dataframe_or_empty("Corporate Actions", service.load_dataset("corporate_actions"))
        with col2:
            _show_dataframe_or_empty("Daily Bars", service.load_dataset("daily_bars"))
            _show_dataframe_or_empty("Adjustment Factors", service.load_dataset("adjustment_factors"))
        corporate_report = service.load_corporate_actions_report()
        total_return_report = service.load_total_return_report()
        adjustment_reconciliation = service.load_adjustment_reconciliation()
        st.subheader("Corporate Actions And Total Return")
        st.json(
            {
                "events": corporate_report.get("event_count", 0) if corporate_report else 0,
                "implemented": corporate_report.get("implemented_action_count", 0) if corporate_report else 0,
                "unprocessed": corporate_report.get("unprocessed_corporate_action_count", 0) if corporate_report else 0,
                "total_return_mode": corporate_report.get("total_return_mode", "") if corporate_report else "",
                "total_return_records": total_return_report.get("records", 0) if total_return_report else 0,
                "cash_dividend_amount": total_return_report.get("cash_dividend_amount", 0.0) if total_return_report else 0.0,
                "adjustment_reconciliation_warnings": adjustment_reconciliation.get("warning_count", 0) if adjustment_reconciliation else 0,
            }
        )
        _show_dataframe_or_empty("Corporate Action Events", service.load_corporate_action_events())
        _show_dataframe_or_empty("Total Return Series", service.load_total_return_series())
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
        broker_file_gateway = service.load_broker_file_gateway_report()
        broker_file_manifest = service.load_broker_file_manifest()
        broker_file_roundtrip = service.load_broker_file_roundtrip_report()
        operator_handoff = service.load_operator_handoff_report()
        mapping_certification = service.load_broker_mapping_certification_decision()
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
                "broker_file_gateway": broker_file_gateway.get("summary", broker_file_gateway) if broker_file_gateway else {},
                "broker_file_manifest": {
                    "file_batch_id": broker_file_manifest.get("file_batch_id") if broker_file_manifest else "",
                    "order_count": broker_file_manifest.get("order_count", broker_file_manifest.get("orders", 0)) if broker_file_manifest else 0,
                },
                "roundtrip": broker_file_roundtrip.get("summary", broker_file_roundtrip) if broker_file_roundtrip else {},
                "operator_handoff": {
                    "status": operator_handoff.get("status", "") if operator_handoff else "",
                    "missing_required_items": operator_handoff.get("missing_required_items", []) if operator_handoff else [],
                },
                "mapping_certification_status": mapping_certification.get("status", "") if mapping_certification else "",
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
        production_orchestrator = service.load_production_orchestrator_report()
        production_plan = service.load_production_run_plan()
        production_readiness = service.load_production_readiness_report()
        production_phases = service.load_production_phase_runs()
        production_gates = service.load_production_gate_results()
        shadow_report = service.load_shadow_run_report()
        shadow_orders = service.load_shadow_orders()
        shadow_fills = service.load_shadow_fills()
        shadow_positions = service.load_shadow_positions()
        shadow_drift = service.load_shadow_drift_report()
        shadow_performance = service.load_shadow_performance_report()
        incident_report = service.load_incident_report()
        incident_records = service.load_incident_records()
        incident_events = service.load_incident_events()
        incident_runbook = service.load_incident_runbook()
        approvals = service.load_approvals()
        approval_log = service.load_approval_log()
        compliance_pack = service.load_program_trading_compliance_pack()
        system_inventory = service.load_program_trading_system_inventory()
        strategy_inventory = service.load_program_trading_strategy_inventory()
        risk_inventory = service.load_program_trading_risk_control_inventory()
        compliance_gaps = service.load_compliance_gap_report()
        secret_scan = service.load_secret_scan_report()
        secret_findings = service.load_secret_scan_findings()
        evidence_records = service.load_program_trading_evidence_records()
        compliance_checklist = service.load_program_trading_compliance_checklist()
        broker_uat_report = service.load_broker_uat_report()
        broker_uat_results = service.load_broker_uat_results()
        adapter_capabilities = service.load_broker_adapter_capability_manifest()
        contract_report = service.load_broker_adapter_contract_report()
        uat_replay = service.load_broker_uat_replay_report()
        broker_connectivity = service.load_broker_connectivity_report()
        broker_connectivity_profile = service.load_broker_connectivity_profile()
        broker_network_guard = service.load_broker_network_guard_report()
        broker_credentials = service.load_broker_credential_ref_manifest()
        broker_connectivity_sessions = service.load_broker_connectivity_sessions()
        broker_connectivity_issues = service.load_broker_connectivity_issues()
        readonly_mirror = service.load_broker_readonly_mirror_report()
        readonly_snapshot = service.load_broker_readonly_snapshot()
        readonly_reconciliation = service.load_readonly_mirror_reconciliation_report()
        readonly_positions = service.load_readonly_broker_positions()
        readonly_orders = service.load_readonly_broker_orders()
        readonly_fills = service.load_readonly_broker_fills()
        go_live_decision = service.load_go_live_gate_decision()
        go_live_scorecard = service.load_go_live_gate_scorecard()
        go_live_checks = service.load_go_live_gate_checks()
        account_state = service.load_paper_account_state()
        settlement_report = service.load_settlement_report()
        settlement_events = service.load_settlement_events()
        cash_buckets = service.load_cash_buckets()
        position_lots = service.load_position_lots()
        position_availability = service.load_position_availability()
        realized_pnl = service.load_realized_pnl()
        account_nav = service.load_account_nav()
        account_reconciliation = service.load_account_reconciliation_report()
        account_performance = service.load_account_performance_report()
        fee_tax_report = service.load_fee_tax_report()
        positions = service.load_paper_positions()
        snapshots = service.load_account_snapshots()
        trade_ledger = service.load_trade_ledger()
        corporate_action_ledger = service.load_corporate_action_ledger()
        monitoring_report = service.load_monitoring_report()
        monitoring_markdown = service.load_monitoring_report_markdown()
        monitoring_alerts = service.load_monitoring_alerts()
        risk_control_report = service.load_risk_control_report()
        risk_control_breaches = service.load_risk_control_breaches()
        risk_limit_usage = service.load_risk_limit_usage()
        kill_switch_state = service.load_kill_switch_state()
        risk_override_records = service.load_risk_override_records()
        broker_report = service.load_broker_report()
        broker_reconciliation = service.load_broker_reconciliation()
        statement_manifest = service.load_broker_statement_manifest()
        statement_import_report = service.load_broker_statement_import_report()
        statement_validation = service.load_broker_statement_validation_report()
        statement_parse_issues = service.load_broker_statement_parse_issues()
        eod_reconciliation = service.load_eod_reconciliation_report()
        reconciliation_breaks = service.load_reconciliation_breaks()
        external_mirror = service.load_external_account_mirror()
        external_cash = service.load_external_mirror_table("cash")
        external_positions = service.load_external_mirror_table("position")
        external_fills = service.load_external_mirror_table("fill")
        adjustment_proposals = service.load_adjustment_proposals()
        adjustment_batch = service.load_adjustment_proposal_batch()
        adjustment_application = service.load_adjustment_application_result()
        adjustment_ledger = service.load_adjustment_ledger()
        model_registry_report = service.load_model_registry_report()
        lifecycle_report = service.load_factor_lifecycle_report()
        production_replay = service.load_production_replay_report()
        production_replay_days = service.load_production_replay_days()
        shadow_lab_report = service.load_shadow_lab_report()
        shadow_day_summaries = service.load_shadow_day_summaries()
        shadow_drift_summary = service.load_shadow_drift_summary()
        shadow_calibration = service.load_shadow_calibration_suggestions()
        live_readiness_decision = service.load_live_readiness_decision()
        live_readiness_scorecard = service.load_live_readiness_scorecard()
        live_readiness_checks = service.load_live_readiness_checks()
        st.subheader("Production Run")
        st.json(
            production_run
            if production_run
            else {"status": "No production_run.json found"}
        )
        if production_markdown:
            st.markdown(production_markdown)
        st.subheader("Production Orchestrator")
        st.json(
            {
                "production_run_id": production_orchestrator.get("production_run_id"),
                "status": production_orchestrator.get("status"),
                "run_mode": production_orchestrator.get("run_mode"),
                "plan_phases": len(production_plan.get("phases", [])) if production_plan else 0,
                "readiness_status": production_readiness.get("status") if production_readiness else "",
                "gate_summary": production_readiness.get("summary", {}) if production_readiness else {},
                "incident_summary": production_orchestrator.get("incident_summary", {}) if production_orchestrator else {},
            }
            if production_orchestrator or production_plan or production_readiness
            else {"status": "No production orchestrator artifacts found"}
        )
        _show_dataframe_or_empty("Production Phase Runs", production_phases)
        _show_dataframe_or_empty("Production Gate Results", production_gates)
        st.subheader("Shadow Trading")
        st.json(
            {
                "status": shadow_report.get("status", "") if shadow_report else "",
                "execution_mode": shadow_report.get("execution_mode", "") if shadow_report else "",
                "summary": shadow_report.get("summary", {}) if shadow_report else {},
                "drift": shadow_drift.get("summary", {}) if shadow_drift else {},
                "performance": shadow_performance.get("metrics", {}) if shadow_performance else {},
            }
            if shadow_report or shadow_drift or shadow_performance
            else {"status": "No shadow trading artifacts found"}
        )
        _show_dataframe_or_empty("Shadow Orders", shadow_orders)
        _show_dataframe_or_empty("Shadow Fills", shadow_fills)
        _show_dataframe_or_empty("Shadow Positions", shadow_positions)
        st.subheader("Production Replay")
        st.json(
            {
                "replay_id": production_replay.get("replay_id"),
                "status": production_replay.get("status"),
                "summary": production_replay.get("summary", {}),
            }
            if production_replay
            else {"status": "No production replay artifacts found"}
        )
        _show_dataframe_or_empty("Production Replay Days", production_replay_days)
        st.subheader("Shadow Lab")
        st.json(
            {
                "status": shadow_lab_report.get("status"),
                "performance": shadow_lab_report.get("performance_summary", {}),
                "drift": shadow_drift_summary or shadow_lab_report.get("drift_summary", {}),
                "calibration_suggestions": shadow_calibration.get("suggestions", []),
            }
            if shadow_lab_report or shadow_drift_summary or shadow_calibration
            else {"status": "No shadow lab artifacts found"}
        )
        _show_dataframe_or_empty("Shadow Day Summaries", shadow_day_summaries)
        st.subheader("Live Readiness")
        st.json(
            {
                "status": live_readiness_decision.get("status"),
                "passed": live_readiness_decision.get("passed"),
                "new_status": live_readiness_decision.get("new_status"),
                "score": live_readiness_decision.get("score", live_readiness_scorecard.get("score")),
                "summary": live_readiness_scorecard.get("summary", {}),
            }
            if live_readiness_decision or live_readiness_scorecard
            else {"status": "No live readiness artifacts found"}
        )
        _show_dataframe_or_empty("Live Readiness Checks", live_readiness_checks)
        st.subheader("Broker Connectivity And Read-Only Mirror")
        st.json(
            {
                "connectivity": {
                    "status": broker_connectivity.get("status", ""),
                    "profile_name": (broker_connectivity.get("summary") or {}).get("profile_name")
                    or broker_connectivity_profile.get("profile_name", ""),
                    "mode": (broker_connectivity.get("summary") or {}).get("connectivity_mode")
                    or broker_connectivity_profile.get("connectivity_mode", ""),
                    "network_guard_status": (broker_connectivity.get("summary") or {}).get("network_guard_status")
                    or (broker_network_guard.get("network_guard") or {}).get("status", ""),
                    "secret_blocker_count": (broker_credentials.get("summary") or {}).get("secret_blocker_count", 0),
                    "real_submit_supported": (broker_connectivity.get("summary") or {}).get("real_submit_supported", False),
                },
                "readonly_mirror": {
                    "status": readonly_mirror.get("status", ""),
                    "snapshot_id": readonly_snapshot.get("snapshot_id", ""),
                    "break_count": readonly_reconciliation.get("break_count", 0),
                    "position_count": (readonly_mirror.get("summary") or {}).get("readonly_position_count", 0),
                    "order_count": (readonly_mirror.get("summary") or {}).get("readonly_order_count", 0),
                    "fill_count": (readonly_mirror.get("summary") or {}).get("readonly_fill_count", 0),
                    "real_submit_supported": (readonly_mirror.get("summary") or {}).get("real_submit_supported", False),
                },
            }
            if broker_connectivity or readonly_mirror
            else {"status": "No broker connectivity or read-only mirror artifacts found"}
        )
        _show_dataframe_or_empty("Broker Connectivity Sessions", broker_connectivity_sessions)
        _show_dataframe_or_empty("Broker Connectivity Issues", broker_connectivity_issues)
        _show_dataframe_or_empty("Read-Only Broker Positions", readonly_positions)
        _show_dataframe_or_empty("Read-Only Broker Orders", readonly_orders)
        _show_dataframe_or_empty("Read-Only Broker Fills", readonly_fills)
        st.subheader("Incidents")
        st.json(
            {
                "summary": incident_report.get("summary", {}) if incident_report else {},
                "runbook_steps": len(incident_runbook.get("steps", [])) if incident_runbook else 0,
            }
            if incident_report or incident_runbook
            else {"status": "No incident artifacts found"}
        )
        _show_dataframe_or_empty("Incident Records", incident_records)
        _show_dataframe_or_empty("Incident Events", incident_events)
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
        _show_dataframe_or_empty("Corporate Action Ledger", corporate_action_ledger)
        st.subheader("Settlement, Lots, PnL And NAV")
        st.json(
            {
                "settlement": {
                    "profile": settlement_report.get("settlement_profile"),
                    "pending": settlement_report.get("pending_settlement_event_count", 0),
                    "failed": settlement_report.get("failed_settlement_event_count", 0),
                    "realized_pnl": settlement_report.get("realized_pnl", 0.0),
                    "unrealized_pnl": settlement_report.get("unrealized_pnl", 0.0),
                    "nav_difference": settlement_report.get("nav_difference", 0.0),
                    "fee_tax_total": settlement_report.get("fee_tax_total", 0.0),
                },
                "account_reconciliation": account_reconciliation,
                "account_performance": account_performance,
                "fee_tax": fee_tax_report,
            }
            if settlement_report
            else {"status": "No settlement_report.json found"}
        )
        _show_dataframe_or_empty("Settlement Events", settlement_events)
        _show_dataframe_or_empty("Cash Buckets", cash_buckets)
        _show_dataframe_or_empty("Position Lots", position_lots)
        _show_dataframe_or_empty("Position Availability", position_availability)
        _show_dataframe_or_empty("Realized PnL", realized_pnl)
        _show_dataframe_or_empty("Account NAV", account_nav)
        st.subheader("Pre-Trade Risk Controls")
        st.json(
            {
                "status": risk_control_report.get("status", "") if risk_control_report else "",
                "accepted_orders": risk_control_report.get("accepted_orders", 0) if risk_control_report else 0,
                "rejected_orders": risk_control_report.get("rejected_orders", 0) if risk_control_report else 0,
                "clipped_orders": risk_control_report.get("clipped_orders", 0) if risk_control_report else 0,
                "warning_count": risk_control_report.get("warning_count", 0) if risk_control_report else 0,
                "error_count": risk_control_report.get("error_count", 0) if risk_control_report else 0,
                "kill_switch_active": kill_switch_state.get("active", False) if kill_switch_state else False,
                "kill_switch_reason": kill_switch_state.get("reason", "") if kill_switch_state else "",
                "override_records": len(risk_override_records),
            }
        )
        _show_dataframe_or_empty("Risk Control Breaches", risk_control_breaches)
        _show_dataframe_or_empty("Risk Limit Usage", risk_limit_usage)
        _show_dataframe_or_empty("Risk Override Records", risk_override_records)
        st.subheader("Broker")
        broker_file_gateway = service.load_broker_file_gateway_report()
        broker_file_roundtrip = service.load_broker_file_roundtrip_report()
        operator_handoff = service.load_operator_handoff_report()
        mapping_certification = service.load_broker_mapping_certification_decision()
        st.json(
            {
                "broker_summary": broker_report.get("summary", {}) if broker_report else {},
                "reconciliation": broker_reconciliation,
                "broker_file_gateway": broker_file_gateway.get("summary", broker_file_gateway) if broker_file_gateway else {},
                "broker_file_roundtrip": broker_file_roundtrip.get("summary", broker_file_roundtrip) if broker_file_roundtrip else {},
                "operator_handoff": {
                    "status": operator_handoff.get("status", "") if operator_handoff else "",
                    "missing_required_items": operator_handoff.get("missing_required_items", []) if operator_handoff else [],
                },
                "mapping_certification_status": mapping_certification.get("status", "") if mapping_certification else "",
            }
        )
        st.subheader("Broker Statement And EOD Reconciliation")
        st.json(
            {
                "statement_id": statement_manifest.get("statement_id", "") if statement_manifest else "",
                "statement_schema": statement_manifest.get("schema_name", "") if statement_manifest else "",
                "synthetic": statement_manifest.get("metadata", {}).get("synthetic") if statement_manifest else None,
                "import_status": statement_import_report.get("status", "") if statement_import_report else "",
                "parse_issues": len(statement_parse_issues),
                "validation_status": statement_validation.get("status", "") if statement_validation else "",
                "eod_status": eod_reconciliation.get("status", "") if eod_reconciliation else "",
                "break_count": eod_reconciliation.get("break_count", 0) if eod_reconciliation else 0,
                "material_break_count": eod_reconciliation.get("material_break_count", 0) if eod_reconciliation else 0,
                "unresolved_break_count": eod_reconciliation.get("unresolved_break_count", 0) if eod_reconciliation else 0,
                "cash_difference": eod_reconciliation.get("cash_difference", 0.0) if eod_reconciliation else 0.0,
                "position_share_difference": eod_reconciliation.get("position_share_difference", 0.0) if eod_reconciliation else 0.0,
                "nav_difference": eod_reconciliation.get("nav_difference", 0.0) if eod_reconciliation else 0.0,
                "external_account_mirror": external_mirror,
                "adjustment_batch": adjustment_batch,
                "adjustment_application": adjustment_application,
            }
        )
        _show_dataframe_or_empty("Statement Parse Issues", statement_parse_issues)
        _show_dataframe_or_empty("External Cash Mirror", external_cash)
        _show_dataframe_or_empty("External Position Mirror", external_positions)
        _show_dataframe_or_empty("External Fill Mirror", external_fills)
        _show_dataframe_or_empty("Reconciliation Breaks", reconciliation_breaks)
        _show_dataframe_or_empty("Adjustment Proposals", adjustment_proposals)
        _show_dataframe_or_empty("Adjustment Ledger", adjustment_ledger)
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
        st.subheader("Pre-Live Compliance / UAT / Gate")
        st.json(
            {
                "compliance_status": compliance_pack.get("status", "") if compliance_pack else "",
                "compliance_gap_count": (compliance_pack.get("summary") or {}).get("gap_count", 0) if compliance_pack else 0,
                "secret_blocker_count": secret_scan.get("blocker_count", 0) if secret_scan else 0,
                "broker_uat_status": broker_uat_report.get("status", "") if broker_uat_report else "",
                "broker_uat_failed_count": (broker_uat_report.get("summary") or {}).get("failed_count", 0) if broker_uat_report else 0,
                "contract_status": contract_report.get("status", "") if contract_report else "",
                "uat_replay_status": uat_replay.get("status", "") if uat_replay else "",
                "go_live_status": go_live_decision.get("status", "") if go_live_decision else "",
                "go_live_required_remediation": len(go_live_decision.get("required_remediation", [])) if go_live_decision else 0,
                "real_broker_submit_supported": system_inventory.get("real_broker_submit_supported", False) if system_inventory else False,
                "adapter_capability": {
                    "real_network_required": adapter_capabilities.get("real_network_required", None) if adapter_capabilities else None,
                    "real_broker_credentials_required": adapter_capabilities.get("real_broker_credentials_required", None) if adapter_capabilities else None,
                },
                "strategy_inventory": {
                    "factor_id": strategy_inventory.get("factor_id", "") if strategy_inventory else "",
                    "portfolio_policy_id": strategy_inventory.get("portfolio_policy_id", "") if strategy_inventory else "",
                },
                "risk_inventory": {
                    "kill_switch_available": risk_inventory.get("kill_switch_available", False) if risk_inventory else False,
                    "settlement_aware": risk_inventory.get("settlement_aware", False) if risk_inventory else False,
                },
                "scorecard_summary": go_live_scorecard.get("summary", {}) if go_live_scorecard else {},
                "gap_report": compliance_gaps.get("status", "") if compliance_gaps else "",
            }
        )
        _show_dataframe_or_empty("Compliance Evidence", evidence_records)
        _show_dataframe_or_empty("Compliance Checklist", compliance_checklist)
        _show_dataframe_or_empty("Secret Scan Findings", secret_findings)
        _show_dataframe_or_empty("Broker UAT Results", broker_uat_results)
        _show_dataframe_or_empty("Go-Live Gate Checks", go_live_checks)

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
