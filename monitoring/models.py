"""Monitoring report dataclasses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class MonitoringAlert:
    severity: str
    check: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MonitoringReport:
    created_at: str
    as_of_date: str
    checks: dict[str, Any]
    alerts: list[MonitoringAlert]

    def to_dict(self) -> dict[str, Any]:
        pit = self.checks.get("point_in_time_validation", {}) if isinstance(self.checks, dict) else {}
        survivorship = self.checks.get("survivorship_bias", {}) if isinstance(self.checks, dict) else {}
        leakage = self.checks.get("leakage_audit", {}) if isinstance(self.checks, dict) else {}
        truncation = self.checks.get("truncation_consistency", {}) if isinstance(self.checks, dict) else {}
        cutoff = self.checks.get("feature_cutoff_policy", {}) if isinstance(self.checks, dict) else {}
        backtest = self.checks.get("backtest", {}) if isinstance(self.checks, dict) else {}
        corporate = self.checks.get("corporate_action_report", {}) if isinstance(self.checks, dict) else {}
        ca_ledger = self.checks.get("corporate_action_ledger", {}) if isinstance(self.checks, dict) else {}
        settlement = self.checks.get("settlement_report", {}) if isinstance(self.checks, dict) else {}
        account_reconciliation = self.checks.get("account_reconciliation", {}) if isinstance(self.checks, dict) else {}
        fee_tax = self.checks.get("settlement_fee_tax", {}) if isinstance(self.checks, dict) else {}
        statement_import = self.checks.get("broker_statement_import", {}) if isinstance(self.checks, dict) else {}
        statement_staleness = self.checks.get("statement_staleness", {}) if isinstance(self.checks, dict) else {}
        eod = self.checks.get("eod_reconciliation", {}) if isinstance(self.checks, dict) else {}
        cash_diff = self.checks.get("external_cash_difference", {}) if isinstance(self.checks, dict) else {}
        position_diff = self.checks.get("external_position_difference", {}) if isinstance(self.checks, dict) else {}
        nav_diff = self.checks.get("external_nav_difference", {}) if isinstance(self.checks, dict) else {}
        proposals = self.checks.get("adjustment_proposals", {}) if isinstance(self.checks, dict) else {}
        application = self.checks.get("adjustment_application", {}) if isinstance(self.checks, dict) else {}
        risk_controls = self.checks.get("pre_trade_risk_controls", {}) if isinstance(self.checks, dict) else {}
        risk_usage = self.checks.get("risk_limit_usage", {}) if isinstance(self.checks, dict) else {}
        kill_switch = self.checks.get("kill_switch_state", {}) if isinstance(self.checks, dict) else {}
        risk_overrides = self.checks.get("risk_overrides", {}) if isinstance(self.checks, dict) else {}
        data_source = self.checks.get("data_source_smoke", {}) if isinstance(self.checks, dict) else {}
        provider = self.checks.get("provider_readiness", {}) if isinstance(self.checks, dict) else {}
        field_coverage = self.checks.get("field_coverage", {}) if isinstance(self.checks, dict) else {}
        data_audit = self.checks.get("data_source_audit", {}) if isinstance(self.checks, dict) else {}
        baseline = self.checks.get("baseline_compare", {}) if isinstance(self.checks, dict) else {}
        backfill_run = self.checks.get("backfill_run", {}) if isinstance(self.checks, dict) else {}
        backfill_coverage = self.checks.get("backfill_coverage", {}) if isinstance(self.checks, dict) else {}
        data_lake_version = self.checks.get("data_lake_version", {}) if isinstance(self.checks, dict) else {}
        research_freeze = self.checks.get("research_freeze", {}) if isinstance(self.checks, dict) else {}
        artifact_schema = self.checks.get("artifact_schema_validation", {}) if isinstance(self.checks, dict) else {}
        release_gate = self.checks.get("release_gate", {}) if isinstance(self.checks, dict) else {}
        package_build = self.checks.get("package_build_artifacts", {}) if isinstance(self.checks, dict) else {}
        compute_resources = self.checks.get("compute_cluster_resources", {}) if isinstance(self.checks, dict) else {}
        compute_failures = self.checks.get("compute_job_failures", {}) if isinstance(self.checks, dict) else {}
        compute_retries = self.checks.get("compute_job_retries", {}) if isinstance(self.checks, dict) else {}
        stale_leases = self.checks.get("stale_gpu_leases", {}) if isinstance(self.checks, dict) else {}
        cuda_oom = self.checks.get("cuda_oom", {}) if isinstance(self.checks, dict) else {}
        cpu_fallbacks = self.checks.get("cpu_fallbacks", {}) if isinstance(self.checks, dict) else {}
        experiment_shards = self.checks.get("experiment_shard_failures", {}) if isinstance(self.checks, dict) else {}
        experiment_merge = self.checks.get("experiment_merge_status", {}) if isinstance(self.checks, dict) else {}
        gpu_throughput = self.checks.get("gpu_throughput_regression", {}) if isinstance(self.checks, dict) else {}
        alpha_campaign = self.checks.get("alpha_factory_campaign", {}) if isinstance(self.checks, dict) else {}
        alpha_static = self.checks.get("alpha_static_errors", {}) if isinstance(self.checks, dict) else {}
        alpha_proxy = self.checks.get("alpha_proxy_eval", {}) if isinstance(self.checks, dict) else {}
        alpha_diversity = self.checks.get("alpha_diversity", {}) if isinstance(self.checks, dict) else {}
        feature_set = self.checks.get("feature_set_manifest", {}) if isinstance(self.checks, dict) else {}
        feature_coverage = self.checks.get("feature_coverage", {}) if isinstance(self.checks, dict) else {}
        validation_lab = self.checks.get("validation_lab", {}) if isinstance(self.checks, dict) else {}
        multiple_testing = self.checks.get("multiple_testing", {}) if isinstance(self.checks, dict) else {}
        overfit_risk = self.checks.get("overfit_risk", {}) if isinstance(self.checks, dict) else {}
        placebo_tests = self.checks.get("placebo_tests", {}) if isinstance(self.checks, dict) else {}
        regime_validation = self.checks.get("regime_validation", {}) if isinstance(self.checks, dict) else {}
        sensitivity_validation = self.checks.get("sensitivity_validation", {}) if isinstance(self.checks, dict) else {}
        stress_validation = self.checks.get("stress_backtest_validation", {}) if isinstance(self.checks, dict) else {}
        factor_certification = self.checks.get("factor_certification", {}) if isinstance(self.checks, dict) else {}
        portfolio_lab = self.checks.get("portfolio_lab", {}) if isinstance(self.checks, dict) else {}
        portfolio_certification = self.checks.get("portfolio_certification", {}) if isinstance(self.checks, dict) else {}
        uncertified = self.checks.get("uncertified_production_candidate", {}) if isinstance(self.checks, dict) else {}
        production = self.checks.get("production_orchestrator", {}) if isinstance(self.checks, dict) else {}
        production_readiness = self.checks.get("production_readiness", {}) if isinstance(self.checks, dict) else {}
        production_phases = self.checks.get("production_phase_failures", {}) if isinstance(self.checks, dict) else {}
        shadow_run = self.checks.get("shadow_trading_run", {}) if isinstance(self.checks, dict) else {}
        shadow_drift = self.checks.get("shadow_drift", {}) if isinstance(self.checks, dict) else {}
        incidents = self.checks.get("incidents", {}) if isinstance(self.checks, dict) else {}
        close_day = self.checks.get("production_close_day_status", {}) if isinstance(self.checks, dict) else {}
        return {
            "created_at": self.created_at,
            "as_of_date": self.as_of_date,
            "checks": self.checks,
            "alerts": [alert.to_dict() for alert in self.alerts],
            "pit_blocker_count": int(pit.get("pit_blocker_count", 0) or 0) if isinstance(pit, dict) else 0,
            "pit_warning_count": int(pit.get("pit_warning_count", 0) or 0) if isinstance(pit, dict) else 0,
            "leakage_blocker_count": int(leakage.get("leakage_blocker_count", 0) or 0) if isinstance(leakage, dict) else 0,
            "leakage_warning_count": int(leakage.get("leakage_warning_count", 0) or 0) if isinstance(leakage, dict) else 0,
            "truncation_consistency_passed": truncation.get("truncation_consistency_passed") if isinstance(truncation, dict) else None,
            "truncation_max_abs_diff": float(truncation.get("truncation_max_abs_diff", 0.0) or 0.0) if isinstance(truncation, dict) else 0.0,
            "survivorship_warning_count": int(survivorship.get("survivorship_warning_count", 0) or 0) if isinstance(survivorship, dict) else 0,
            "current_only_security_master": bool(survivorship.get("current_only_security_master", False)) if isinstance(survivorship, dict) else False,
            "active_universe_coverage": float(pit.get("active_universe_coverage", 0.0) or 0.0) if isinstance(pit, dict) else 0.0,
            "inactive_security_order_count": int(backtest.get("inactive_security_order_count", 0) or 0) if isinstance(backtest, dict) else 0,
            "feature_cutoff_mode": str(cutoff.get("feature_cutoff_mode", "")) if isinstance(cutoff, dict) else "",
            "corporate_action_event_count": int(corporate.get("corporate_action_event_count", 0) or 0) if isinstance(corporate, dict) else 0,
            "unprocessed_corporate_action_count": int(corporate.get("unprocessed_corporate_action_count", 0) or 0) if isinstance(corporate, dict) else 0,
            "corporate_action_ledger_entries": int(ca_ledger.get("corporate_action_ledger_entries", 0) or 0) if isinstance(ca_ledger, dict) else 0,
            "corporate_action_cash_amount": float(ca_ledger.get("corporate_action_cash_amount", 0.0) or 0.0) if isinstance(ca_ledger, dict) else 0.0,
            "pending_settlement_event_count": int(settlement.get("pending_settlement_event_count", 0) or 0) if isinstance(settlement, dict) else 0,
            "failed_settlement_event_count": int(settlement.get("failed_settlement_event_count", 0) or 0) if isinstance(settlement, dict) else 0,
            "settlement_reconciliation_error_count": int(settlement.get("settlement_reconciliation_error_count", 0) or 0) if isinstance(settlement, dict) else 0,
            "settlement_nav_difference": float(settlement.get("nav_difference", 0.0) or 0.0) if isinstance(settlement, dict) else 0.0,
            "account_reconciliation_error_count": int(account_reconciliation.get("error_count", 0) or 0) if isinstance(account_reconciliation, dict) else 0,
            "total_fee_tax": float(fee_tax.get("total_fee_tax", 0.0) or 0.0) if isinstance(fee_tax, dict) else 0.0,
            "broker_statement_imported": bool(statement_import.get("broker_statement_imported", False)) if isinstance(statement_import, dict) else False,
            "broker_statement_parse_error_count": int(statement_import.get("broker_statement_parse_error_count", 0) or 0) if isinstance(statement_import, dict) else 0,
            "statement_stale": bool(statement_staleness.get("statement_stale", False)) if isinstance(statement_staleness, dict) else False,
            "eod_reconciliation_status": str(eod.get("eod_reconciliation_status", "")) if isinstance(eod, dict) else "",
            "reconciliation_break_count": int(eod.get("reconciliation_break_count", 0) or 0) if isinstance(eod, dict) else 0,
            "material_break_count": int(eod.get("material_break_count", 0) or 0) if isinstance(eod, dict) else 0,
            "unresolved_break_count": int(eod.get("unresolved_break_count", 0) or 0) if isinstance(eod, dict) else 0,
            "external_cash_difference": float(cash_diff.get("external_cash_difference", 0.0) or 0.0) if isinstance(cash_diff, dict) else 0.0,
            "external_position_difference": float(position_diff.get("external_position_difference", 0.0) or 0.0) if isinstance(position_diff, dict) else 0.0,
            "external_nav_difference": float(nav_diff.get("external_nav_difference", 0.0) or 0.0) if isinstance(nav_diff, dict) else 0.0,
            "unmatched_external_fill_count": int(eod.get("unmatched_external_fill_count", 0) or 0) if isinstance(eod, dict) else 0,
            "unmatched_internal_fill_count": int(eod.get("unmatched_internal_fill_count", 0) or 0) if isinstance(eod, dict) else 0,
            "fee_tax_difference": float(eod.get("fee_tax_difference", 0.0) or 0.0) if isinstance(eod, dict) else 0.0,
            "adjustment_proposal_count": int(proposals.get("adjustment_proposal_count", 0) or 0) if isinstance(proposals, dict) else 0,
            "adjustment_application_count": int(application.get("adjustment_application_count", 0) or 0) if isinstance(application, dict) else 0,
            "adjustment_pending_approval_count": int(proposals.get("adjustment_pending_approval_count", 0) or 0) if isinstance(proposals, dict) else 0,
            "risk_control_status": str(risk_controls.get("risk_control_status", "")) if isinstance(risk_controls, dict) else "",
            "risk_control_rejected_orders": int(risk_controls.get("risk_control_rejected_orders", 0) or 0) if isinstance(risk_controls, dict) else 0,
            "risk_control_clipped_orders": int(risk_controls.get("risk_control_clipped_orders", 0) or 0) if isinstance(risk_controls, dict) else 0,
            "risk_control_warning_count": int(risk_controls.get("risk_control_warning_count", 0) or 0) if isinstance(risk_controls, dict) else 0,
            "risk_control_error_count": int(risk_controls.get("risk_control_error_count", 0) or 0) if isinstance(risk_controls, dict) else 0,
            "risk_limit_usage_records": int(risk_usage.get("risk_limit_usage_records", 0) or 0) if isinstance(risk_usage, dict) else 0,
            "risk_limit_breached_records": int(risk_usage.get("risk_limit_breached_records", 0) or 0) if isinstance(risk_usage, dict) else 0,
            "kill_switch_active": bool(kill_switch.get("kill_switch_active", False)) if isinstance(kill_switch, dict) else False,
            "active_risk_overrides": int(risk_overrides.get("active_risk_overrides", 0) or 0) if isinstance(risk_overrides, dict) else 0,
            "provider_status": str(data_source.get("provider_status", "")) if isinstance(data_source, dict) else "",
            "provider_error_count": int(data_source.get("provider_error_count", 0) or 0) if isinstance(data_source, dict) else 0,
            "provider_warning_count": int(data_source.get("provider_warning_count", 0) or 0) if isinstance(data_source, dict) else 0,
            "api_permission_issue_count": int(provider.get("api_permission_issue_count", 0) or 0) if isinstance(provider, dict) else 0,
            "rate_limit_issue_count": int(provider.get("rate_limit_issue_count", 0) or 0) if isinstance(provider, dict) else 0,
            "missing_field_count": int(field_coverage.get("missing_field_count", 0) or 0) if isinstance(field_coverage, dict) else 0,
            "empty_dataset_count": int(field_coverage.get("empty_dataset_count", 0) or 0) if isinstance(field_coverage, dict) else 0,
            "data_source_cache_hit_rate": float(data_audit.get("data_source_cache_hit_rate", 0.0) or 0.0) if isinstance(data_audit, dict) else 0.0,
            "baseline_diff_count": int(baseline.get("baseline_diff_count", 0) or 0) if isinstance(baseline, dict) else 0,
            "backfill_status": str(backfill_run.get("backfill_status", "")) if isinstance(backfill_run, dict) else "",
            "backfill_failed_jobs": int(backfill_run.get("backfill_failed_jobs", 0) or 0) if isinstance(backfill_run, dict) else 0,
            "backfill_coverage_gap_count": int(backfill_coverage.get("backfill_coverage_gap_count", 0) or 0) if isinstance(backfill_coverage, dict) else 0,
            "dataset_version_id": str(data_lake_version.get("dataset_version_id", "")) if isinstance(data_lake_version, dict) else "",
            "dataset_content_hash": str(data_lake_version.get("dataset_content_hash", "")) if isinstance(data_lake_version, dict) else "",
            "freeze_validation_status": str(research_freeze.get("freeze_validation_status", "")) if isinstance(research_freeze, dict) else "",
            "data_hash_drift_count": int(research_freeze.get("data_hash_drift_count", 0) or 0) if isinstance(research_freeze, dict) else 0,
            "artifact_schema_error_count": int(artifact_schema.get("artifact_schema_error_count", 0) or 0) if isinstance(artifact_schema, dict) else 0,
            "artifact_schema_warning_count": int(artifact_schema.get("artifact_schema_warning_count", 0) or 0) if isinstance(artifact_schema, dict) else 0,
            "release_gate_status": str(release_gate.get("release_gate_status", "")) if isinstance(release_gate, dict) else "",
            "package_build_status": str(package_build.get("package_build_status", "")) if isinstance(package_build, dict) else "",
            "cuda_available": bool(compute_resources.get("cuda_available", False)) if isinstance(compute_resources, dict) else False,
            "gpu_count_detected": int(compute_resources.get("gpu_count_detected", 0) or 0) if isinstance(compute_resources, dict) else 0,
            "gpu_count_used": int(gpu_throughput.get("gpu_count_used", 0) or 0) if isinstance(gpu_throughput, dict) else 0,
            "compute_job_count": int(compute_failures.get("compute_job_count", 0) or 0) if isinstance(compute_failures, dict) else 0,
            "compute_failed_job_count": int(compute_failures.get("compute_failed_job_count", 0) or 0) if isinstance(compute_failures, dict) else 0,
            "compute_resumed_job_count": int(compute_retries.get("compute_resumed_job_count", 0) or 0) if isinstance(compute_retries, dict) else 0,
            "compute_timeout_count": int(compute_failures.get("compute_timeout_count", 0) or 0) if isinstance(compute_failures, dict) else 0,
            "stale_gpu_lease_count": int(stale_leases.get("stale_gpu_lease_count", 0) or 0) if isinstance(stale_leases, dict) else 0,
            "cuda_oom_count": int(cuda_oom.get("cuda_oom_count", 0) or 0) if isinstance(cuda_oom, dict) else 0,
            "fallback_to_cpu_count": int(cpu_fallbacks.get("fallback_to_cpu_count", 0) or 0) if isinstance(cpu_fallbacks, dict) else 0,
            "total_gpu_allocated_seconds": float(compute_failures.get("total_gpu_allocated_seconds", 0.0) or 0.0) if isinstance(compute_failures, dict) else 0.0,
            "formula_eval_throughput": float(gpu_throughput.get("formula_eval_throughput", 0.0) or 0.0) if isinstance(gpu_throughput, dict) else 0.0,
            "pretrain_samples_per_second": float(gpu_throughput.get("pretrain_samples_per_second", 0.0) or 0.0) if isinstance(gpu_throughput, dict) else 0.0,
            "experiment_status": str(experiment_shards.get("experiment_status", "")) if isinstance(experiment_shards, dict) else "",
            "experiment_shard_count": int(experiment_shards.get("experiment_shard_count", 0) or 0) if isinstance(experiment_shards, dict) else 0,
            "experiment_failed_shard_count": int(experiment_shards.get("experiment_failed_shard_count", 0) or 0) if isinstance(experiment_shards, dict) else 0,
            "experiment_merge_status": str(experiment_merge.get("experiment_merge_status", "")) if isinstance(experiment_merge, dict) else "",
            "scheduler_warning_count": int(gpu_throughput.get("scheduler_warning_count", 0) or 0) if isinstance(gpu_throughput, dict) else 0,
            "alpha_campaign_id": alpha_campaign.get("alpha_campaign_id") if isinstance(alpha_campaign, dict) else None,
            "alpha_candidates_generated": int(alpha_campaign.get("alpha_candidates_generated", 0) or 0) if isinstance(alpha_campaign, dict) else 0,
            "alpha_static_pass_count": int(alpha_static.get("alpha_static_pass_count", alpha_campaign.get("alpha_static_pass_count", 0)) or 0) if isinstance(alpha_static, dict) else 0,
            "alpha_static_error_count": int(alpha_static.get("alpha_static_error_count", alpha_campaign.get("alpha_static_error_count", 0)) or 0) if isinstance(alpha_static, dict) else 0,
            "alpha_proxy_pass_count": int(alpha_proxy.get("alpha_proxy_pass_count", alpha_campaign.get("alpha_proxy_pass_count", 0)) or 0) if isinstance(alpha_proxy, dict) else 0,
            "alpha_full_eval_count": int(alpha_campaign.get("alpha_full_eval_count", 0) or 0) if isinstance(alpha_campaign, dict) else 0,
            "alpha_shortlist_count": int(alpha_diversity.get("alpha_shortlist_count", alpha_campaign.get("alpha_shortlist_count", 0)) or 0) if isinstance(alpha_diversity, dict) else 0,
            "alpha_feature_count": int(feature_set.get("feature_count", alpha_campaign.get("alpha_feature_count", 0)) or 0) if isinstance(feature_set, dict) else 0,
            "alpha_family_count": int(alpha_diversity.get("alpha_family_count", alpha_campaign.get("alpha_family_count", 0)) or 0) if isinstance(alpha_diversity, dict) else 0,
            "alpha_best_score": float(alpha_campaign.get("alpha_best_score", 0.0) or 0.0) if isinstance(alpha_campaign, dict) else 0.0,
            "alpha_diversity_warning_count": int(alpha_diversity.get("alpha_diversity_warning_count", 0) or 0) if isinstance(alpha_diversity, dict) else 0,
            "feature_coverage_warning_count": int(feature_coverage.get("feature_coverage_warning_count", 0) or 0) if isinstance(feature_coverage, dict) else 0,
            "feature_set_hash": feature_set.get("feature_set_hash") if isinstance(feature_set, dict) else None,
            "validation_status": str(validation_lab.get("validation_status", "")) if isinstance(validation_lab, dict) else "",
            "validation_blocker_count": int(validation_lab.get("validation_blocker_count", 0) or 0) if isinstance(validation_lab, dict) else 0,
            "validation_warning_count": int(validation_lab.get("validation_warning_count", 0) or 0) if isinstance(validation_lab, dict) else 0,
            "validation_out_of_sample_score": float(validation_lab.get("out_of_sample_score", 0.0) or 0.0) if isinstance(validation_lab, dict) else 0.0,
            "validation_window_pass_ratio": float(validation_lab.get("window_pass_ratio", 0.0) or 0.0) if isinstance(validation_lab, dict) else 0.0,
            "effective_trial_count": int(multiple_testing.get("effective_trial_count", 0) or 0) if isinstance(multiple_testing, dict) else 0,
            "selection_bias_warning": bool(multiple_testing.get("selection_bias_warning", False)) if isinstance(multiple_testing, dict) else False,
            "pbo_estimate": float(overfit_risk.get("pbo_estimate", 0.0) or 0.0) if isinstance(overfit_risk, dict) else 0.0,
            "deflated_ic_score": float(overfit_risk.get("deflated_ic_score", 0.0) or 0.0) if isinstance(overfit_risk, dict) else 0.0,
            "overfit_risk_level": str(overfit_risk.get("overfit_risk_level", "")) if isinstance(overfit_risk, dict) else "",
            "placebo_percentile": float(placebo_tests.get("placebo_percentile", 0.0) or 0.0) if isinstance(placebo_tests, dict) else 0.0,
            "null_exceedance_ratio": float(placebo_tests.get("null_exceedance_ratio", 0.0) or 0.0) if isinstance(placebo_tests, dict) else 0.0,
            "regime_pass_ratio": float(regime_validation.get("regime_pass_ratio", 0.0) or 0.0) if isinstance(regime_validation, dict) else 0.0,
            "sensitivity_pass_ratio": float(sensitivity_validation.get("sensitivity_pass_ratio", 0.0) or 0.0) if isinstance(sensitivity_validation, dict) else 0.0,
            "stress_backtest_pass_ratio": float(stress_validation.get("stress_backtest_pass_ratio", 0.0) or 0.0) if isinstance(stress_validation, dict) else 0.0,
            "certification_status": str(factor_certification.get("certification_status", "")) if isinstance(factor_certification, dict) else "",
            "certification_passed": bool(factor_certification.get("certification_passed", False)) if isinstance(factor_certification, dict) else False,
            "certification_blocker_count": int(factor_certification.get("certification_blocker_count", 0) or 0) if isinstance(factor_certification, dict) else 0,
            "certification_required_remediation_count": int(factor_certification.get("certification_required_remediation_count", 0) or 0) if isinstance(factor_certification, dict) else 0,
            "uncertified_production_candidate": bool(uncertified.get("uncertified_production_candidate", False)) if isinstance(uncertified, dict) else False,
            "portfolio_lab_status": str(portfolio_lab.get("portfolio_lab_status", "")) if isinstance(portfolio_lab, dict) else "",
            "portfolio_lab_trial_count": int(portfolio_lab.get("portfolio_lab_trial_count", 0) or 0) if isinstance(portfolio_lab, dict) else 0,
            "selected_portfolio_policy_id": str(portfolio_lab.get("selected_portfolio_policy_id", "") or "") if isinstance(portfolio_lab, dict) else "",
            "portfolio_certification_status": str(portfolio_certification.get("portfolio_certification_status", "")) if isinstance(portfolio_certification, dict) else "",
            "portfolio_certification_passed": bool(portfolio_certification.get("portfolio_certification_passed", False)) if isinstance(portfolio_certification, dict) else False,
            "portfolio_certification_blocker_count": int(portfolio_certification.get("portfolio_certification_blocker_count", 0) or 0) if isinstance(portfolio_certification, dict) else 0,
            "production_run_id": production.get("production_run_id") if isinstance(production, dict) else None,
            "production_run_status": str(production.get("production_run_status", "")) if isinstance(production, dict) else "",
            "production_run_mode": str(production.get("production_run_mode", "")) if isinstance(production, dict) else "",
            "production_phase_failed_count": int(production_phases.get("production_phase_failed_count", production.get("production_phase_failed_count", 0)) or 0) if isinstance(production_phases, dict) else 0,
            "production_phase_blocked_count": int(production_phases.get("production_phase_blocked_count", production.get("production_phase_blocked_count", 0)) or 0) if isinstance(production_phases, dict) else 0,
            "production_gate_blocker_count": int(production_readiness.get("production_gate_blocker_count", 0) or 0) if isinstance(production_readiness, dict) else 0,
            "production_gate_warning_count": int(production_readiness.get("production_gate_warning_count", 0) or 0) if isinstance(production_readiness, dict) else 0,
            "shadow_run_status": str(shadow_run.get("shadow_run_status", "")) if isinstance(shadow_run, dict) else "",
            "shadow_order_count": int(shadow_run.get("shadow_order_count", 0) or 0) if isinstance(shadow_run, dict) else 0,
            "shadow_fill_rate": float(shadow_run.get("shadow_fill_rate", 0.0) or 0.0) if isinstance(shadow_run, dict) else 0.0,
            "shadow_target_weight_drift": float(shadow_drift.get("shadow_target_weight_drift", 0.0) or 0.0) if isinstance(shadow_drift, dict) else 0.0,
            "shadow_position_weight_drift": float(shadow_drift.get("shadow_position_weight_drift", 0.0) or 0.0) if isinstance(shadow_drift, dict) else 0.0,
            "incident_open_count": int(incidents.get("incident_open_count", 0) or 0) if isinstance(incidents, dict) else 0,
            "incident_critical_count": int(incidents.get("incident_critical_count", 0) or 0) if isinstance(incidents, dict) else 0,
            "incident_unresolved_count": int(incidents.get("incident_unresolved_count", 0) or 0) if isinstance(incidents, dict) else 0,
            "close_day_status": str(close_day.get("close_day_status", production.get("close_day_status", "")) or "") if isinstance(close_day, dict) else "",
        }
