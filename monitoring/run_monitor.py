"""CLI for local production monitoring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from factor_store import LocalFactorStore

from .checks import (
    check_active_risk_drift,
    check_active_model_status,
    check_artifact_schema_validation,
    check_attribution_anomaly,
    check_broker_file_outbox,
    check_broker_idempotency,
    check_broker_reconciliation,
    check_broker_statement_import,
    check_broker_rejected_orders,
    check_baseline_compare,
    check_open_broker_orders,
    check_capacity_warnings,
    check_data_source_audit,
    check_data_source_smoke,
    check_data_freshness,
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
    check_alphagpt_pretrain,
    check_alphagpt_checkpoint_manifest,
    check_impact_cost_spike,
    check_model_lifecycle_health,
    check_model_lineage_completeness,
    check_model_registry,
    check_model_rollback_state,
    check_order_fill_quality,
    check_paper_account,
    check_package_build_artifacts,
    check_pre_trade_risk_controls,
    check_point_in_time_validation,
    check_provider_readiness,
    check_quality_report,
    check_release_gate,
    check_risk_limit_usage,
    check_kill_switch_state,
    check_risk_overrides,
    check_risk_report,
    check_account_reconciliation,
    check_pending_model_reviews,
    check_quarantined_or_paused_model_usage,
    check_style_exposure_drift,
    check_survivorship_bias,
    check_leakage_audit,
    check_truncation_consistency,
    check_total_return_report,
    check_active_universe_coverage,
    check_feature_cutoff_policy,
    check_settlement_fee_tax,
    check_settlement_report,
    check_statement_staleness,
    check_unfilled_orders,
    check_unresolved_reconciliation_breaks,
    check_material_reconciliation_breaks,
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
    parser.add_argument("--data-source-smoke-report-path")
    parser.add_argument("--field-coverage-path")
    parser.add_argument("--audit-summary-path")
    parser.add_argument("--baseline-compare-path")
    parser.add_argument("--artifact-validation-report-path")
    parser.add_argument("--release-gate-report-path")
    parser.add_argument("--release-manifest-path")
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
        ("field_coverage", lambda: check_field_coverage(args.field_coverage_path)),
        ("data_source_audit", lambda: check_data_source_audit(args.audit_summary_path)),
        ("corporate_action_report", lambda: check_corporate_action_report(args.corporate_action_report_path or _default_corporate_action_path(args.orders_dir, "corporate_actions_report.json"))),
        ("total_return_report", lambda: check_total_return_report(args.total_return_report_path or _default_corporate_action_path(args.orders_dir, "total_return_report.json"))),
        ("corporate_action_ledger", lambda: check_corporate_action_ledger(args.corporate_action_ledger_path or args.paper_account_dir)),
        ("baseline_compare", lambda: check_baseline_compare(args.baseline_compare_path)),
        ("artifact_schema_validation", lambda: check_artifact_schema_validation(args.artifact_validation_report_path)),
        ("release_gate", lambda: check_release_gate(args.release_gate_report_path)),
        ("package_build_artifacts", lambda: check_package_build_artifacts(args.release_manifest_path)),
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
