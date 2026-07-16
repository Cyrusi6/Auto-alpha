"""CLI for local production monitoring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from factor_store import LocalFactorStore

from .checks import (
    check_active_risk_drift,
    check_alpha_diversity,
    check_alpha_factory_campaign,
    check_alpha_proxy_eval,
    check_alpha_shortlist,
    check_alpha_experiment_store,
    check_alpha_dedup_report,
    check_alpha_validation_pool,
    check_alpha_large_campaign_plan,
    check_factor_certification_queue,
    check_alpha_static_errors,
    check_active_model_status,
    check_artifact_schema_validation,
    check_attribution_anomaly,
    check_broker_file_outbox,
    check_broker_idempotency,
    check_broker_reconciliation,
    check_broker_statement_import,
    check_broker_rejected_orders,
    check_broker_file_gateway_report,
    check_broker_connectivity,
    check_broker_readonly_mirror,
    check_broker_mapping_certification,
    check_broker_uat_contract,
    check_baseline_compare,
    check_backfill_repair,
    check_backfill_eta,
    check_backfill_failed_jobs,
    check_backfill_quarantined_jobs,
    check_backfill_stalled_dataset,
    check_backfill_coverage,
    check_backfill_run,
    check_compute_cluster_resources,
    check_compute_job_failures,
    check_compute_job_retries,
    check_cpu_fallbacks,
    check_cuda_oom,
    check_open_broker_orders,
    check_capacity_warnings,
    check_data_source_audit,
    check_data_source_smoke,
    check_data_lake_version,
    check_data_freshness,
    check_data_quality_blockers,
    check_data_quality_freeze_gate,
    check_data_quality_lab,
    check_core_dataset_semantic_quality,
    check_cross_dataset_quality,
    check_corporate_action_ledger,
    check_corporate_action_report,
    check_adjustment_application,
    check_adjustment_proposals,
    check_execution_quality,
    check_eod_reconciliation,
    check_external_cash_difference,
    check_external_nav_difference,
    check_external_position_difference,
    check_factor_risk_concentration,
    check_factor_drift,
    check_field_coverage,
    check_formula_batch_eval,
    check_formula_corpus,
    check_factor_certification,
    check_factor_certification_campaign,
    check_portfolio_certification,
    check_portfolio_lab,
    check_certified_factor_pool,
    check_experiment_merge_status,
    check_experiment_shard_failures,
    check_alphagpt_pretrain,
    check_alphagpt_checkpoint_manifest,
    check_gpu_availability,
    check_gpu_throughput_regression,
    check_impact_cost_spike,
    check_model_lifecycle_health,
    check_model_lineage_completeness,
    check_model_registry,
    check_model_rollback_state,
    check_order_fill_quality,
    check_operator_handoff_report,
    check_paper_account,
    check_package_build_artifacts,
    check_postprocess_plan_blockers,
    check_post_download_blockers,
    check_post_download_plan,
    check_post_download_step_runs,
    check_pre_trade_risk_controls,
    check_production_close_day_status,
    check_production_replay,
    check_production_gate_blockers,
    check_production_orchestrator,
    check_production_phase_failures,
    check_production_readiness,
    check_point_in_time_validation,
    check_provider_readiness,
    check_quality_report,
    check_raw_data_index,
    check_raw_data_landing,
    check_raw_freeze_readiness,
    check_research_data_readiness,
    check_research_readiness_final,
    check_release_gate,
    check_expanded_dataset_pit_safety,
    check_freeze_candidate_package,
    check_feature_readiness,
    check_replay_day_failures,
    check_readiness_remediation,
    check_api_permission_matrix,
    check_matrix_freshness,
    check_matrix_refresh,
    check_research_freeze,
    check_risk_limit_usage,
    check_kill_switch_state,
    check_risk_overrides,
    check_risk_report,
    check_real_data_readiness,
    check_real_data_size,
    check_real_data_sla,
    check_real_data_token_redaction,
    check_running_backfill_progress,
    check_account_reconciliation,
    check_pending_model_reviews,
    check_quarantined_or_paused_model_usage,
    check_multiple_testing,
    check_style_exposure_drift,
    check_survivorship_bias,
    check_leakage_audit,
    check_truncation_consistency,
    check_total_return_report,
    check_active_universe_coverage,
    check_feature_coverage,
    check_feature_cutoff_policy,
    check_alpha_factory_v3_readiness,
    check_blocked_features_used,
    check_feature_pit_alignment,
    check_feature_promotion_approval,
    check_feature_promotion_expiry,
    check_feature_promotion_policy,
    check_feature_set_manifest,
    check_feature_set_v3,
    check_unreviewed_weak_pit_features,
    check_v3_feature_family_readiness,
    check_weak_pit_features,
    check_overfit_risk,
    check_placebo_tests,
    check_regime_validation,
    check_sensitivity_validation,
    check_stress_backtest_validation,
    check_task054c_engineering_baseline,
    check_task055a_simulator_baseline,
    check_task055b_security_date_remediation,
    check_task055c_security_date_closure,
    check_task055d_secure_remediation,
    check_task055e_offline_source_salvage,
    check_uncertified_production_candidate,
    check_validation_lab,
    check_validation_campaign_leaderboard,
    check_validation_campaign_store,
    check_validation_large_campaign_plan,
    check_portfolio_campaign,
    check_production_candidate_bundle,
    check_optimizer_policy_activation_queue,
    check_settlement_fee_tax,
    check_settlement_report,
    check_shadow_drift,
    check_shadow_drift_aggregate,
    check_shadow_calibration_suggestions,
    check_shadow_lab,
    check_shadow_trading_run,
    check_statement_staleness,
    check_stale_gpu_leases,
    check_unfilled_orders,
    check_incidents,
    check_runbook_completion,
    check_unresolved_critical_incidents,
    check_unresolved_reconciliation_breaks,
    check_material_reconciliation_breaks,
    check_live_readiness,
    check_go_live_gate,
    check_go_live_required_remediation,
    check_manual_review_status,
    check_no_real_submit_path,
    check_program_trading_compliance_pack,
    check_secret_scan,
    check_multi_day_incident_trend,
)
from .report import build_monitoring_report, write_monitoring_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate local production monitoring artifacts.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--paper-account-dir", required=True)
    parser.add_argument("--orders-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--factor-id")
    parser.add_argument("--risk-report-path")
    parser.add_argument("--risk-exposures-path")
    parser.add_argument("--risk-decomposition-path")
    parser.add_argument("--return-attribution-path")
    parser.add_argument("--capacity-report-path")
    parser.add_argument("--execution-quality-path")
    parser.add_argument("--broker-store-dir")
    parser.add_argument("--broker-batch-id")
    parser.add_argument("--broker-reconciliation-path")
    parser.add_argument("--broker-outbox-manifest-path")
    parser.add_argument("--broker-file-gateway-report-path")
    parser.add_argument("--broker-file-batch-path")
    parser.add_argument("--broker-file-roundtrip-report-path")
    parser.add_argument("--broker-file-roundtrip-issues-path")
    parser.add_argument("--broker-connectivity-report-path")
    parser.add_argument("--broker-connectivity-profile-path")
    parser.add_argument("--broker-readonly-mirror-report-path")
    parser.add_argument("--broker-readonly-snapshot-path")
    parser.add_argument("--broker-network-guard-report-path")
    parser.add_argument("--broker-credential-ref-manifest-path")
    parser.add_argument("--readonly-mirror-reconciliation-report-path")
    parser.add_argument("--operator-handoff-report-path")
    parser.add_argument("--operator-handoff-checklist-path")
    parser.add_argument("--broker-mapping-certification-decision-path")
    parser.add_argument("--broker-mapping-certification-scorecard-path")
    parser.add_argument("--data-source-smoke-report-path")
    parser.add_argument("--field-coverage-path")
    parser.add_argument("--audit-summary-path")
    parser.add_argument("--baseline-compare-path")
    parser.add_argument("--backfill-run-report-path")
    parser.add_argument("--backfill-coverage-report-path")
    parser.add_argument("--backfill-observer-report-path")
    parser.add_argument("--backfill-dataset-progress-path")
    parser.add_argument("--backfill-eta-report-path")
    parser.add_argument("--backfill-repair-plan-path")
    parser.add_argument("--backfill-repair-run-report-path")
    parser.add_argument("--backfill-repair-batch-plan-path")
    parser.add_argument("--backfill-postprocess-plan-path")
    parser.add_argument("--raw-data-landing-report-path")
    parser.add_argument("--raw-freeze-readiness-decision-path")
    parser.add_argument("--raw-data-index-manifest-path")
    parser.add_argument("--raw-data-index-report-path")
    parser.add_argument("--raw-data-index-validation-report-path")
    parser.add_argument("--data-quality-lab-report-path")
    parser.add_argument("--data-quality-scorecard-path")
    parser.add_argument("--data-quality-freeze-gate-path")
    parser.add_argument("--data-quality-issues-path")
    parser.add_argument("--cross-dataset-quality-report-path")
    parser.add_argument("--research-data-readiness-report-path")
    parser.add_argument("--research-readiness-decision-path")
    parser.add_argument("--feature-readiness-catalog-path")
    parser.add_argument("--post-download-plan-path")
    parser.add_argument("--post-download-run-report-path")
    parser.add_argument("--post-download-step-runs-path")
    parser.add_argument("--freeze-candidate-package-path")
    parser.add_argument("--dataset-version-manifest-path")
    parser.add_argument("--freeze-validation-report-path")
    parser.add_argument("--artifact-validation-report-path")
    parser.add_argument("--release-gate-report-path")
    parser.add_argument("--release-manifest-path")
    parser.add_argument("--compute-run-report-path")
    parser.add_argument("--compute-resource-snapshot-path")
    parser.add_argument("--compute-jobs-path")
    parser.add_argument("--compute-job-runs-path")
    parser.add_argument("--experiment-run-report-path")
    parser.add_argument("--experiment-plan-path")
    parser.add_argument("--experiment-merge-report-path")
    parser.add_argument("--gpu-benchmark-report-path")
    parser.add_argument("--risk-control-report-path")
    parser.add_argument("--risk-control-breaches-path")
    parser.add_argument("--risk-limit-usage-path")
    parser.add_argument("--kill-switch-state-path")
    parser.add_argument("--risk-override-records-path")
    parser.add_argument("--formula-corpus-stats-path")
    parser.add_argument("--formula-batch-eval-result-path")
    parser.add_argument("--alphagpt-pretrain-result-path")
    parser.add_argument("--alphagpt-checkpoint-manifest-path")
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--model-version-id")
    parser.add_argument("--factor-lifecycle-report-path")
    parser.add_argument("--model-review-package-path")
    parser.add_argument("--model-lineage-graph-path")
    parser.add_argument("--pit-validation-report-path")
    parser.add_argument("--survivorship-report-path")
    parser.add_argument("--leakage-audit-report-path")
    parser.add_argument("--truncation-consistency-report-path")
    parser.add_argument("--corporate-action-report-path")
    parser.add_argument("--corporate-action-ledger-path")
    parser.add_argument("--total-return-report-path")
    parser.add_argument("--adjustment-reconciliation-path")
    parser.add_argument("--settlement-report-path")
    parser.add_argument("--settlement-events-path")
    parser.add_argument("--cash-buckets-path")
    parser.add_argument("--position-lots-path")
    parser.add_argument("--position-availability-path")
    parser.add_argument("--realized-pnl-path")
    parser.add_argument("--account-nav-path")
    parser.add_argument("--account-reconciliation-report-path")
    parser.add_argument("--account-performance-report-path")
    parser.add_argument("--fee-tax-report-path")
    parser.add_argument("--broker-statement-manifest-path")
    parser.add_argument("--broker-statement-import-report-path")
    parser.add_argument("--eod-reconciliation-report-path")
    parser.add_argument("--reconciliation-breaks-path")
    parser.add_argument("--external-account-mirror-path")
    parser.add_argument("--adjustment-proposals-path")
    parser.add_argument("--adjustment-application-result-path")
    parser.add_argument("--alpha-factory-report-path")
    parser.add_argument("--alpha-campaign-manifest-path")
    parser.add_argument("--alpha-generation-stats-path")
    parser.add_argument("--alpha-static-checks-path")
    parser.add_argument("--alpha-proxy-eval-report-path")
    parser.add_argument("--alpha-diversity-report-path")
    parser.add_argument("--alpha-shortlist-path")
    parser.add_argument("--feature-set-manifest-path")
    parser.add_argument("--feature-coverage-report-path")
    parser.add_argument("--feature-family-readiness-path")
    parser.add_argument("--feature-pit-alignment-report-path")
    parser.add_argument("--feature-build-warnings-path")
    parser.add_argument("--feature-promotion-policy-path")
    parser.add_argument("--feature-promotion-evidence-report-path")
    parser.add_argument("--feature-promotion-review-package-path")
    parser.add_argument("--feature-promotion-decisions-path")
    parser.add_argument("--feature-promotion-allowlist-path")
    parser.add_argument("--feature-promotion-application-report-path")
    parser.add_argument("--validation-lab-report-path")
    parser.add_argument("--factor-validation-summary-path")
    parser.add_argument("--multiple-testing-report-path")
    parser.add_argument("--overfit-risk-report-path")
    parser.add_argument("--placebo-test-report-path")
    parser.add_argument("--regime-validation-report-path")
    parser.add_argument("--sensitivity-report-path")
    parser.add_argument("--stress-backtest-report-path")
    parser.add_argument("--factor-certification-decision-path")
    parser.add_argument("--factor-certification-scorecard-path")
    parser.add_argument("--portfolio-lab-report-path")
    parser.add_argument("--portfolio-robustness-report-path")
    parser.add_argument("--portfolio-policy-trials-path")
    parser.add_argument("--selected-portfolio-policy-path")
    parser.add_argument("--portfolio-certification-decision-path")
    parser.add_argument("--portfolio-certification-scorecard-path")
    parser.add_argument("--certified-portfolio-policy-path")
    parser.add_argument("--production-orchestrator-report-path")
    parser.add_argument("--production-run-plan-path")
    parser.add_argument("--production-readiness-report-path")
    parser.add_argument("--production-phase-runs-path")
    parser.add_argument("--production-gate-results-path")
    parser.add_argument("--production-runbook-path")
    parser.add_argument("--shadow-run-report-path")
    parser.add_argument("--shadow-drift-report-path")
    parser.add_argument("--incident-report-path")
    parser.add_argument("--incident-records-path")
    parser.add_argument("--incident-runbook-path")
    parser.add_argument("--production-replay-report-path")
    parser.add_argument("--production-replay-days-path")
    parser.add_argument("--shadow-lab-report-path")
    parser.add_argument("--shadow-drift-summary-path")
    parser.add_argument("--shadow-calibration-suggestions-path")
    parser.add_argument("--live-readiness-decision-path")
    parser.add_argument("--live-readiness-scorecard-path")
    parser.add_argument("--program-trading-compliance-pack-path")
    parser.add_argument("--secret-scan-report-path")
    parser.add_argument("--broker-uat-report-path")
    parser.add_argument("--broker-adapter-contract-report-path")
    parser.add_argument("--go-live-gate-decision-path")
    parser.add_argument("--go-live-gate-scorecard-path")
    parser.add_argument("--real-data-readiness-report-path")
    parser.add_argument("--real-data-pipeline-report-path")
    parser.add_argument("--real-data-sla-report-path")
    parser.add_argument("--real-data-size-report-path")
    parser.add_argument("--provider-readiness-matrix-path")
    parser.add_argument("--api-permission-matrix-path")
    parser.add_argument("--required-dataset-status-path")
    parser.add_argument("--matrix-refresh-result-path")
    parser.add_argument("--matrix-freshness-report-path")
    parser.add_argument("--alpha-experiment-store-report-path")
    parser.add_argument("--alpha-experiment-registry-path")
    parser.add_argument("--alpha-factor-dedup-report-path")
    parser.add_argument("--alpha-validation-candidate-pool-path")
    parser.add_argument("--alpha-large-campaign-plan-path")
    parser.add_argument("--validation-campaign-store-report-path")
    parser.add_argument("--validation-campaign-registry-path")
    parser.add_argument("--validation-leaderboard-path")
    parser.add_argument("--factor-certification-queue-path")
    parser.add_argument("--validation-large-campaign-plan-path")
    parser.add_argument("--task054c-final-verification-path")
    parser.add_argument("--task055a-final-report-path")
    parser.add_argument("--task055b-final-report-path")
    parser.add_argument("--task055c-final-report-path")
    parser.add_argument("--task055d-final-report-path")
    parser.add_argument("--task055e-offline-report-path")
    parser.add_argument("--factor-certification-campaign-report-path")
    parser.add_argument("--factor-certification-campaign-registry-path")
    parser.add_argument("--certified-factor-pool-path")
    parser.add_argument("--certified-factor-leaderboard-path")
    parser.add_argument("--portfolio-campaign-report-path")
    parser.add_argument("--portfolio-campaign-registry-path")
    parser.add_argument("--production-candidate-bundle-path")
    parser.add_argument("--optimizer-policy-activation-queue-path")
    parser.add_argument("--production-candidate-bundle-plan-path")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    checks = {}
    alerts = []
    for name, func in [
        ("data_freshness", lambda: check_data_freshness(args.data_dir, args.as_of_date)),
        ("quality_report", lambda: check_quality_report(args.data_dir)),
        ("factor_drift", lambda: check_factor_drift(LocalFactorStore(args.factor_store_dir), args.factor_id)),
        ("risk_report", lambda: check_risk_report(args.risk_report_path or _default_risk_path(args.orders_dir))),
        ("style_exposure_drift", lambda: check_style_exposure_drift(args.risk_exposures_path or _default_path(args.orders_dir, "risk_exposures.jsonl"))),
        ("active_risk_drift", lambda: check_active_risk_drift(args.risk_decomposition_path or _default_path(args.orders_dir, "risk_decomposition.jsonl"))),
        ("factor_risk_concentration", lambda: check_factor_risk_concentration(args.risk_report_path or _default_risk_path(args.orders_dir))),
        ("attribution_anomaly", lambda: check_attribution_anomaly(args.return_attribution_path or _default_path(args.orders_dir, "return_attribution.jsonl"))),
        ("capacity_warnings", lambda: check_capacity_warnings(args.capacity_report_path or _default_plan_path(args.orders_dir, "capacity_report.json"))),
        ("execution_quality", lambda: check_execution_quality(args.execution_quality_path or _default_plan_path(args.orders_dir, "execution_quality.json"))),
        ("unfilled_orders", lambda: check_unfilled_orders(args.execution_quality_path or _default_plan_path(args.orders_dir, "execution_quality.json"))),
        ("impact_cost_spike", lambda: check_impact_cost_spike(args.capacity_report_path or _default_plan_path(args.orders_dir, "capacity_report.json"))),
        (
            "broker_reconciliation",
            lambda: check_broker_reconciliation(args.broker_reconciliation_path or _default_broker_path(args.orders_dir, "broker_reconciliation.json")),
        ),
        ("open_broker_orders", lambda: check_open_broker_orders(args.broker_store_dir or _default_broker_store(args.orders_dir), args.broker_batch_id)),
        ("broker_rejected_orders", lambda: check_broker_rejected_orders(args.broker_store_dir or _default_broker_store(args.orders_dir), args.broker_batch_id)),
        ("broker_idempotency", lambda: check_broker_idempotency(args.broker_store_dir or _default_broker_store(args.orders_dir), args.broker_batch_id)),
        (
            "broker_file_outbox",
            lambda: check_broker_file_outbox(args.broker_outbox_manifest_path or _default_broker_outbox_manifest(args.orders_dir)),
        ),
        ("broker_file_gateway", lambda: check_broker_file_gateway_report(args.broker_file_gateway_report_path)),
        (
            "broker_connectivity",
            lambda: check_broker_connectivity(
                args.broker_connectivity_report_path,
                args.broker_network_guard_report_path,
                args.broker_credential_ref_manifest_path,
            ),
        ),
        (
            "broker_readonly_mirror",
            lambda: check_broker_readonly_mirror(
                args.broker_readonly_mirror_report_path,
                args.readonly_mirror_reconciliation_report_path,
            ),
        ),
        ("operator_handoff", lambda: check_operator_handoff_report(args.operator_handoff_report_path)),
        ("broker_mapping_certification", lambda: check_broker_mapping_certification(args.broker_mapping_certification_decision_path)),
        ("program_trading_compliance", lambda: check_program_trading_compliance_pack(args.program_trading_compliance_pack_path)),
        ("secret_scan", lambda: check_secret_scan(args.secret_scan_report_path)),
        ("broker_uat_contract", lambda: check_broker_uat_contract(args.broker_uat_report_path, args.broker_adapter_contract_report_path)),
        ("go_live_gate", lambda: check_go_live_gate(args.go_live_gate_decision_path, args.go_live_gate_scorecard_path)),
        ("go_live_required_remediation", lambda: check_go_live_required_remediation(args.go_live_gate_decision_path)),
        ("no_real_submit_path", lambda: check_no_real_submit_path(args.program_trading_compliance_pack_path, args.go_live_gate_decision_path)),
        ("manual_review_status", lambda: check_manual_review_status(args.go_live_gate_decision_path)),
        ("pre_trade_risk_controls", lambda: check_pre_trade_risk_controls(args.risk_control_report_path or _default_risk_control_path(args.orders_dir, "risk_control_report.json"))),
        ("risk_limit_usage", lambda: check_risk_limit_usage(args.risk_limit_usage_path or _default_risk_control_path(args.orders_dir, "risk_limit_usage.jsonl"))),
        ("kill_switch_state", lambda: check_kill_switch_state(args.kill_switch_state_path or _default_risk_control_path(args.orders_dir, "kill_switch_state.json"))),
        ("risk_overrides", lambda: check_risk_overrides(args.risk_override_records_path or _default_risk_control_path(args.orders_dir, "risk_override_records.jsonl"))),
        ("broker_statement_import", lambda: check_broker_statement_import(args.broker_statement_import_report_path)),
        ("statement_staleness", lambda: check_statement_staleness(args.broker_statement_manifest_path, args.as_of_date)),
        ("eod_reconciliation", lambda: check_eod_reconciliation(args.eod_reconciliation_report_path)),
        ("unresolved_reconciliation_breaks", lambda: check_unresolved_reconciliation_breaks(args.reconciliation_breaks_path)),
        ("material_reconciliation_breaks", lambda: check_material_reconciliation_breaks(args.reconciliation_breaks_path)),
        ("external_cash_difference", lambda: check_external_cash_difference(args.eod_reconciliation_report_path)),
        ("external_position_difference", lambda: check_external_position_difference(args.eod_reconciliation_report_path)),
        ("external_nav_difference", lambda: check_external_nav_difference(args.eod_reconciliation_report_path)),
        ("adjustment_proposals", lambda: check_adjustment_proposals(args.adjustment_proposals_path)),
        ("adjustment_application", lambda: check_adjustment_application(args.adjustment_application_result_path)),
        ("data_source_smoke", lambda: check_data_source_smoke(args.data_source_smoke_report_path)),
        ("provider_readiness", lambda: check_provider_readiness(args.data_source_smoke_report_path)),
        ("real_data_readiness", lambda: check_real_data_readiness(args.real_data_readiness_report_path or args.real_data_pipeline_report_path)),
        ("api_permission_matrix", lambda: check_api_permission_matrix(args.api_permission_matrix_path)),
        ("real_data_sla", lambda: check_real_data_sla(args.real_data_sla_report_path)),
        ("real_data_size", lambda: check_real_data_size(args.real_data_size_report_path)),
        ("matrix_refresh", lambda: check_matrix_refresh(args.matrix_refresh_result_path)),
        ("matrix_freshness", lambda: check_matrix_freshness(args.matrix_freshness_report_path)),
        ("real_data_token_redaction", lambda: check_real_data_token_redaction(args.real_data_readiness_report_path or args.real_data_pipeline_report_path)),
        ("field_coverage", lambda: check_field_coverage(args.field_coverage_path)),
        ("data_source_audit", lambda: check_data_source_audit(args.audit_summary_path)),
        ("running_backfill_progress", lambda: check_running_backfill_progress(args.backfill_observer_report_path, args.backfill_dataset_progress_path)),
        ("backfill_eta", lambda: check_backfill_eta(args.backfill_eta_report_path)),
        ("backfill_failed_jobs", lambda: check_backfill_failed_jobs(args.backfill_dataset_progress_path)),
        ("backfill_quarantined_jobs", lambda: check_backfill_quarantined_jobs(args.backfill_dataset_progress_path)),
        ("backfill_stalled_dataset", lambda: check_backfill_stalled_dataset(args.backfill_observer_report_path)),
        ("backfill_repair", lambda: check_backfill_repair(args.backfill_repair_run_report_path, args.backfill_repair_batch_plan_path or args.backfill_repair_plan_path)),
        ("raw_data_landing", lambda: check_raw_data_landing(args.raw_data_landing_report_path)),
        (
            "raw_data_index",
            lambda: check_raw_data_index(
                args.raw_data_index_manifest_path,
                args.raw_data_index_report_path,
                args.raw_data_index_validation_report_path,
            ),
        ),
        (
            "data_quality_lab",
            lambda: check_data_quality_lab(
                args.data_quality_lab_report_path,
                args.data_quality_scorecard_path,
                args.data_quality_freeze_gate_path,
                args.data_quality_issues_path,
            ),
        ),
        (
            "data_quality_blockers",
            lambda: check_data_quality_blockers(args.data_quality_scorecard_path, args.data_quality_freeze_gate_path),
        ),
        (
            "core_dataset_semantic_quality",
            lambda: check_core_dataset_semantic_quality(args.data_quality_freeze_gate_path),
        ),
        ("cross_dataset_quality", lambda: check_cross_dataset_quality(args.cross_dataset_quality_report_path)),
        ("data_quality_freeze_gate", lambda: check_data_quality_freeze_gate(args.data_quality_freeze_gate_path)),
        ("raw_freeze_readiness", lambda: check_raw_freeze_readiness(args.raw_freeze_readiness_decision_path)),
        ("postprocess_plan_blockers", lambda: check_postprocess_plan_blockers(args.backfill_postprocess_plan_path)),
        ("research_data_readiness", lambda: check_research_data_readiness(args.research_data_readiness_report_path or args.research_readiness_decision_path)),
        ("feature_readiness", lambda: check_feature_readiness(args.feature_readiness_catalog_path)),
        ("post_download_plan", lambda: check_post_download_plan(args.post_download_plan_path)),
        ("post_download_blockers", lambda: check_post_download_blockers(args.post_download_run_report_path)),
        ("post_download_step_runs", lambda: check_post_download_step_runs(args.post_download_step_runs_path)),
        ("freeze_candidate_package", lambda: check_freeze_candidate_package(args.freeze_candidate_package_path)),
        ("research_readiness_final", lambda: check_research_readiness_final(args.research_readiness_decision_path)),
        ("expanded_dataset_pit_safety", lambda: check_expanded_dataset_pit_safety(args.research_data_readiness_report_path)),
        ("alpha_factory_campaign", lambda: check_alpha_factory_campaign(args.alpha_factory_report_path, args.alpha_campaign_manifest_path)),
        ("alpha_static_errors", lambda: check_alpha_static_errors(args.alpha_static_checks_path)),
        ("alpha_proxy_eval", lambda: check_alpha_proxy_eval(args.alpha_proxy_eval_report_path)),
        ("alpha_diversity", lambda: check_alpha_diversity(args.alpha_diversity_report_path)),
        ("alpha_shortlist", lambda: check_alpha_shortlist(args.alpha_shortlist_path)),
        (
            "alpha_experiment_store",
            lambda: check_alpha_experiment_store(args.alpha_experiment_store_report_path, args.alpha_experiment_registry_path),
        ),
        ("alpha_dedup_report", lambda: check_alpha_dedup_report(args.alpha_factor_dedup_report_path)),
        ("alpha_validation_pool", lambda: check_alpha_validation_pool(args.alpha_validation_candidate_pool_path)),
        ("alpha_large_campaign_plan", lambda: check_alpha_large_campaign_plan(args.alpha_large_campaign_plan_path)),
        (
            "validation_campaign_store",
            lambda: check_validation_campaign_store(args.validation_campaign_store_report_path, args.validation_campaign_registry_path),
        ),
        ("validation_campaign_leaderboard", lambda: check_validation_campaign_leaderboard(args.validation_leaderboard_path)),
        ("factor_certification_queue", lambda: check_factor_certification_queue(args.factor_certification_queue_path)),
        ("validation_large_campaign_plan", lambda: check_validation_large_campaign_plan(args.validation_large_campaign_plan_path)),
        (
            "factor_certification_campaign",
            lambda: check_factor_certification_campaign(
                args.factor_certification_campaign_report_path,
                args.factor_certification_campaign_registry_path,
            ),
        ),
        ("certified_factor_pool", lambda: check_certified_factor_pool(args.certified_factor_pool_path)),
        (
            "portfolio_campaign",
            lambda: check_portfolio_campaign(args.portfolio_campaign_report_path, args.portfolio_campaign_registry_path),
        ),
        ("production_candidate_bundle", lambda: check_production_candidate_bundle(args.production_candidate_bundle_path)),
        ("optimizer_policy_activation_queue", lambda: check_optimizer_policy_activation_queue(args.optimizer_policy_activation_queue_path)),
        ("feature_set_manifest", lambda: check_feature_set_manifest(args.feature_set_manifest_path)),
        ("feature_coverage", lambda: check_feature_coverage(args.feature_coverage_report_path)),
        ("feature_set_v3", lambda: check_feature_set_v3(args.feature_set_manifest_path)),
        ("v3_feature_family_readiness", lambda: check_v3_feature_family_readiness(args.feature_family_readiness_path)),
        ("weak_pit_features", lambda: check_weak_pit_features(args.feature_pit_alignment_report_path)),
        ("feature_pit_alignment", lambda: check_feature_pit_alignment(args.feature_pit_alignment_report_path)),
        ("alpha_factory_v3_readiness", lambda: check_alpha_factory_v3_readiness(args.feature_family_readiness_path)),
        ("feature_promotion_policy", lambda: check_feature_promotion_policy(args.feature_promotion_policy_path)),
        ("unreviewed_weak_pit_features", lambda: check_unreviewed_weak_pit_features(args.feature_promotion_evidence_report_path)),
        ("blocked_features_used", lambda: check_blocked_features_used(args.feature_promotion_application_report_path)),
        ("feature_promotion_expiry", lambda: check_feature_promotion_expiry(args.feature_promotion_decisions_path)),
        (
            "feature_promotion_approval",
            lambda: check_feature_promotion_approval(args.feature_promotion_review_package_path, args.feature_promotion_allowlist_path),
        ),
        ("validation_lab", lambda: check_validation_lab(args.validation_lab_report_path, args.factor_validation_summary_path)),
        ("multiple_testing", lambda: check_multiple_testing(args.multiple_testing_report_path)),
        ("overfit_risk", lambda: check_overfit_risk(args.overfit_risk_report_path)),
        ("placebo_tests", lambda: check_placebo_tests(args.placebo_test_report_path)),
        ("regime_validation", lambda: check_regime_validation(args.regime_validation_report_path)),
        ("sensitivity_validation", lambda: check_sensitivity_validation(args.sensitivity_report_path)),
        ("stress_backtest_validation", lambda: check_stress_backtest_validation(args.stress_backtest_report_path)),
        ("task054c_engineering_baseline", lambda: check_task054c_engineering_baseline(args.task054c_final_verification_path)),
        ("task055a_simulator_baseline", lambda: check_task055a_simulator_baseline(args.task055a_final_report_path)),
        ("task055b_security_date_remediation", lambda: check_task055b_security_date_remediation(args.task055b_final_report_path)),
        ("task055c_security_date_closure", lambda: check_task055c_security_date_closure(args.task055c_final_report_path)),
        ("task055d_secure_remediation", lambda: check_task055d_secure_remediation(args.task055d_final_report_path)),
        ("task055e_offline_source_salvage", lambda: check_task055e_offline_source_salvage(args.task055e_offline_report_path)),
        (
            "factor_certification",
            lambda: check_factor_certification(args.factor_certification_decision_path, args.factor_certification_scorecard_path),
        ),
        (
            "portfolio_lab",
            lambda: check_portfolio_lab(
                args.portfolio_lab_report_path,
                args.portfolio_robustness_report_path,
                args.portfolio_policy_trials_path,
                args.selected_portfolio_policy_path,
            ),
        ),
        (
            "portfolio_certification",
            lambda: check_portfolio_certification(
                args.portfolio_certification_decision_path,
                args.portfolio_certification_scorecard_path,
                args.certified_portfolio_policy_path,
            ),
        ),
        (
            "uncertified_production_candidate",
            lambda: check_uncertified_production_candidate(LocalFactorStore(args.factor_store_dir), args.factor_certification_decision_path),
        ),
        ("production_orchestrator", lambda: check_production_orchestrator(args.production_orchestrator_report_path)),
        (
            "production_readiness",
            lambda: check_production_readiness(args.production_readiness_report_path, args.production_gate_results_path),
        ),
        ("production_phase_failures", lambda: check_production_phase_failures(args.production_phase_runs_path)),
        ("production_gate_blockers", lambda: check_production_gate_blockers(args.production_gate_results_path)),
        ("production_close_day_status", lambda: check_production_close_day_status(args.production_orchestrator_report_path)),
        ("production_replay", lambda: check_production_replay(args.production_replay_report_path)),
        ("replay_day_failures", lambda: check_replay_day_failures(args.production_replay_days_path)),
        ("shadow_trading_run", lambda: check_shadow_trading_run(args.shadow_run_report_path)),
        ("shadow_drift", lambda: check_shadow_drift(args.shadow_drift_report_path)),
        ("shadow_lab", lambda: check_shadow_lab(args.shadow_lab_report_path)),
        ("shadow_drift_aggregate", lambda: check_shadow_drift_aggregate(args.shadow_drift_summary_path)),
        ("shadow_calibration_suggestions", lambda: check_shadow_calibration_suggestions(args.shadow_calibration_suggestions_path)),
        ("live_readiness", lambda: check_live_readiness(args.live_readiness_decision_path, args.live_readiness_scorecard_path)),
        ("readiness_remediation", lambda: check_readiness_remediation(args.live_readiness_decision_path)),
        ("multi_day_incident_trend", lambda: check_multi_day_incident_trend(args.production_replay_report_path)),
        ("incidents", lambda: check_incidents(args.incident_report_path, args.incident_records_path)),
        ("unresolved_critical_incidents", lambda: check_unresolved_critical_incidents(args.incident_report_path)),
        ("runbook_completion", lambda: check_runbook_completion(args.incident_runbook_path or args.production_runbook_path)),
        ("corporate_action_report", lambda: check_corporate_action_report(args.corporate_action_report_path or _default_corporate_action_path(args.orders_dir, "corporate_actions_report.json"))),
        ("total_return_report", lambda: check_total_return_report(args.total_return_report_path or _default_corporate_action_path(args.orders_dir, "total_return_report.json"))),
        ("corporate_action_ledger", lambda: check_corporate_action_ledger(args.corporate_action_ledger_path or args.paper_account_dir)),
        ("baseline_compare", lambda: check_baseline_compare(args.baseline_compare_path)),
        ("backfill_run", lambda: check_backfill_run(args.backfill_run_report_path)),
        ("backfill_coverage", lambda: check_backfill_coverage(args.backfill_coverage_report_path)),
        ("data_lake_version", lambda: check_data_lake_version(args.dataset_version_manifest_path)),
        ("research_freeze", lambda: check_research_freeze(args.freeze_validation_report_path)),
        ("artifact_schema_validation", lambda: check_artifact_schema_validation(args.artifact_validation_report_path)),
        ("release_gate", lambda: check_release_gate(args.release_gate_report_path)),
        ("package_build_artifacts", lambda: check_package_build_artifacts(args.release_manifest_path)),
        ("compute_cluster_resources", lambda: check_compute_cluster_resources(args.compute_resource_snapshot_path)),
        ("gpu_availability", lambda: check_gpu_availability(args.compute_resource_snapshot_path)),
        ("compute_job_failures", lambda: check_compute_job_failures(args.compute_run_report_path)),
        ("compute_job_retries", lambda: check_compute_job_retries(args.compute_run_report_path)),
        ("stale_gpu_leases", lambda: check_stale_gpu_leases(_default_compute_path(args.compute_run_report_path, "gpu_leases.jsonl"))),
        ("cuda_oom", lambda: check_cuda_oom(args.compute_run_report_path)),
        ("cpu_fallbacks", lambda: check_cpu_fallbacks(args.compute_run_report_path)),
        ("experiment_shard_failures", lambda: check_experiment_shard_failures(args.experiment_run_report_path)),
        ("experiment_merge_status", lambda: check_experiment_merge_status(args.experiment_merge_report_path)),
        ("gpu_throughput_regression", lambda: check_gpu_throughput_regression(args.gpu_benchmark_report_path)),
        ("formula_corpus", lambda: check_formula_corpus(args.formula_corpus_stats_path)),
        ("formula_batch_eval", lambda: check_formula_batch_eval(args.formula_batch_eval_result_path)),
        ("alphagpt_pretrain", lambda: check_alphagpt_pretrain(args.alphagpt_pretrain_result_path)),
        ("alphagpt_checkpoint_manifest", lambda: check_alphagpt_checkpoint_manifest(args.alphagpt_checkpoint_manifest_path)),
        ("model_registry", lambda: check_model_registry(args.model_registry_dir)),
        ("active_model_status", lambda: check_active_model_status(args.model_registry_dir, args.model_version_id)),
        ("model_lifecycle_health", lambda: check_model_lifecycle_health(args.factor_lifecycle_report_path)),
        ("pending_model_reviews", lambda: check_pending_model_reviews(_default_approval_store(args.orders_dir))),
        ("model_lineage_completeness", lambda: check_model_lineage_completeness(args.model_lineage_graph_path)),
        ("model_rollback_state", lambda: check_model_rollback_state(args.model_registry_dir)),
        ("quarantined_or_paused_model_usage", lambda: check_quarantined_or_paused_model_usage(args.model_registry_dir)),
        ("point_in_time_validation", lambda: check_point_in_time_validation(args.pit_validation_report_path)),
        ("survivorship_bias", lambda: check_survivorship_bias(args.survivorship_report_path)),
        ("leakage_audit", lambda: check_leakage_audit(args.leakage_audit_report_path)),
        ("truncation_consistency", lambda: check_truncation_consistency(args.truncation_consistency_report_path)),
        ("active_universe_coverage", lambda: check_active_universe_coverage(args.pit_validation_report_path)),
        ("feature_cutoff_policy", lambda: check_feature_cutoff_policy(args.pit_validation_report_path)),
        ("settlement_report", lambda: check_settlement_report(args.settlement_report_path or _default_settlement_path(args.orders_dir, "settlement_report.json"))),
        (
            "account_reconciliation",
            lambda: check_account_reconciliation(
                args.account_reconciliation_report_path or _default_settlement_path(args.orders_dir, "account_reconciliation_report.json")
            ),
        ),
        ("settlement_fee_tax", lambda: check_settlement_fee_tax(args.fee_tax_report_path or _default_settlement_path(args.orders_dir, "fee_tax_report.json"))),
        ("fill_quality", lambda: check_order_fill_quality(Path(args.orders_dir) / "paper_fills.jsonl")),
        ("paper_account", lambda: check_paper_account(args.paper_account_dir)),
    ]:
        payload, check_alerts = func()
        checks[name] = payload
        alerts.extend(check_alerts)
    report = build_monitoring_report(args.as_of_date, checks, alerts)
    json_path, md_path, alerts_path = write_monitoring_report(report, args.output_dir)
    payload = report.to_dict() | {
        "paths": {
            "monitoring_report_path": str(json_path),
            "monitoring_report_md_path": str(md_path),
            "alerts_path": str(alerts_path),
        }
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if not any(alert.severity == "error" for alert in alerts) else 1


def _default_risk_path(orders_dir: str | Path) -> str:
    root = Path(orders_dir)
    for name in ("risk_model_report.json", "risk_report.json"):
        path = root / name
        if path.exists():
            return str(path)
    return ""


def _default_path(orders_dir: str | Path, filename: str) -> str:
    path = Path(orders_dir) / filename
    return str(path) if path.exists() else ""


def _default_compute_path(anchor_path: str | Path | None, filename: str) -> str:
    if not anchor_path:
        return ""
    root = Path(anchor_path).parent
    path = root / filename
    return str(path) if path.exists() else ""


def _default_plan_path(orders_dir: str | Path, filename: str) -> str:
    root = Path(orders_dir)
    for path in (root / filename, root / "plan" / filename):
        if path.exists():
            return str(path)
    return ""


def _default_broker_path(orders_dir: str | Path, filename: str) -> str:
    root = Path(orders_dir)
    for path in (root / filename, root / "broker" / filename, root.parent / "production_execute" / "broker" / filename):
        if path.exists():
            return str(path)
    return ""


def _default_broker_store(orders_dir: str | Path) -> str:
    root = Path(orders_dir)
    for path in (root / "broker", root.parent / "broker"):
        if (path / "broker_order_state.json").exists():
            return str(path)
    return ""


def _default_broker_outbox_manifest(orders_dir: str | Path) -> str:
    root = Path(orders_dir)
    for path in (
        root / "broker_instruction_manifest.json",
        root / "outbox" / "broker_instruction_manifest.json",
        root.parent / "broker_file" / "outbox" / "broker_instruction_manifest.json",
    ):
        if path.exists():
            return str(path)
    return ""


def _default_risk_control_path(orders_dir: str | Path, filename: str) -> str:
    root = Path(orders_dir)
    for path in (
        root / filename,
        root / "risk_controls" / filename,
        root.parent / "risk_controls" / filename,
        root.parent / "production" / "risk_controls" / filename,
        root.parent / "production_execute" / "risk_controls" / filename,
        root.parent / "backtest" / "risk_controls" / filename,
    ):
        if path.exists():
            return str(path)
    return ""


def _default_corporate_action_path(orders_dir: str | Path, filename: str) -> str:
    root = Path(orders_dir)
    for path in (
        root / filename,
        root / "corporate_actions" / filename,
        root.parent / "corporate_actions" / filename,
        root.parent / "production" / "corporate_actions" / filename,
        root.parent / "production_execute" / "corporate_actions" / filename,
        root.parent / "suite" / "corporate_actions" / filename,
    ):
        if path.exists():
            return str(path)
    return ""


def _default_approval_store(orders_dir: str | Path) -> str:
    root = Path(orders_dir)
    for path in (root.parent / "approvals", root.parent / "model_approvals", root / "approvals"):
        if path.exists():
            return str(path)
    return ""


def _default_settlement_path(orders_dir: str | Path, filename: str) -> str:
    root = Path(orders_dir)
    for path in (
        root / filename,
        root / "settlement" / filename,
        root.parent / "settlement" / "orders" / filename,
        root.parent / "settlement" / "backtest" / filename,
        root.parent / "production_execute" / "settlement" / filename,
        root.parent / "production" / "settlement" / filename,
    ):
        if path.exists():
            return str(path)
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
