"""CLI for daily local production runs."""

from __future__ import annotations

import argparse
import json

from .daily_runner import ProductionDailyRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local daily production workflow.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-freeze-id")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--freeze-validation-report-path")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--approval-store-dir", required=True)
    parser.add_argument("--paper-account-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--orders-dir", required=True)
    parser.add_argument("--factor-id")
    parser.add_argument("--latest-production", action="store_true")
    parser.add_argument("--rebalance-date")
    parser.add_argument("--portfolio-method", choices=["equal_weight", "risk_aware"], default="equal_weight")
    parser.add_argument("--portfolio-policy-path")
    parser.add_argument("--portfolio-certification-decision-path")
    parser.add_argument("--certified-portfolio-policy-path")
    parser.add_argument("--require-certified-portfolio-policy", action="store_true")
    parser.add_argument("--active-optimizer-policy", action="store_true")
    parser.add_argument("--require-active-optimizer-policy", action="store_true")
    parser.add_argument("--portfolio-policy-model-version-id")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--max-weight", type=float, default=0.10)
    parser.add_argument("--portfolio-value", type=float, default=1_000_000.0)
    parser.add_argument("--use-factor-risk-model", action="store_true")
    parser.add_argument("--risk-model-lookback", type=int)
    parser.add_argument("--risk-model-shrinkage", type=float, default=0.1)
    parser.add_argument("--max-style-exposure", type=float)
    parser.add_argument("--max-active-style-exposure", type=float)
    parser.add_argument("--capacity-aware", action="store_true")
    parser.add_argument("--execution-plan-dir")
    parser.add_argument("--max-participation", type=float, default=0.10)
    parser.add_argument("--execution-buckets", default="open")
    parser.add_argument("--broker-adapter", choices=["paper", "simulated", "file"], default="paper")
    parser.add_argument("--broker-store-dir")
    parser.add_argument("--broker-outbox-dir")
    parser.add_argument("--broker-inbox-dir")
    parser.add_argument("--broker-auto-fill", action="store_true", default=True)
    parser.add_argument("--broker-reconcile", action="store_true")
    parser.add_argument("--broker-price-type", default="MARKET")
    parser.add_argument("--broker-file-gateway", action="store_true")
    parser.add_argument("--broker-file-profile", default="generic_broker_csv")
    parser.add_argument("--broker-file-profile-config")
    parser.add_argument("--broker-file-gateway-store-dir")
    parser.add_argument("--broker-file-outbox-dir")
    parser.add_argument("--broker-file-inbox-dir")
    parser.add_argument("--broker-file-handoff-dir")
    parser.add_argument("--operator-handoff-store-dir")
    parser.add_argument("--operator-handoff-approval-store-dir")
    parser.add_argument("--mapping-certification-decision-path")
    parser.add_argument("--require-mapping-certification", action="store_true")
    parser.add_argument("--file-outbox-dry-run", action="store_true")
    parser.add_argument("--auto-confirm-local-smoke", action="store_true")
    parser.add_argument("--use-model-registry", action="store_true")
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--model-version-id")
    parser.add_argument("--require-active-model", action="store_true")
    parser.add_argument("--allow-production-candidate-fallback", action="store_true")
    parser.add_argument("--model-kind", default="composite_factor")
    parser.add_argument("--model-environment", default="paper")
    parser.add_argument("--block-paused-model", action="store_true", default=True)
    parser.add_argument("--block-quarantined-model", action="store_true", default=True)
    parser.add_argument("--require-approval", action="store_true")
    parser.add_argument("--approval-id")
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="same_day_after_close")
    parser.add_argument("--min-listing-days", type=int, default=0)
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument("--apply-corporate-actions", action="store_true")
    parser.add_argument("--corporate-action-dir")
    parser.add_argument("--corporate-action-output-dir")
    parser.add_argument(
        "--target-return-mode",
        choices=["adjusted_close", "raw_close", "corporate_action_total_return"],
        default="adjusted_close",
    )
    parser.add_argument("--corporate-action-application-date-mode", default="pay_date")
    parser.add_argument("--corporate-action-cash-field", default="cash_div")
    parser.add_argument("--reconcile-adjustment-factors", action="store_true")
    parser.add_argument("--settlement-aware", action="store_true")
    parser.add_argument("--settlement-dir")
    parser.add_argument(
        "--settlement-profile",
        choices=["cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"],
        default="cn_ashare_paper_default",
    )
    parser.add_argument("--cost-basis-method", choices=["average", "fifo"], default="average")
    parser.add_argument("--settle-before-trading", action="store_true")
    parser.add_argument("--settle-through-date")
    parser.add_argument("--enforce-available-cash", action="store_true")
    parser.add_argument("--enforce-available-shares", action="store_true")
    parser.add_argument("--allow-unsettled-cash-for-buy", action="store_true")
    parser.add_argument("--allow-unsettled-shares-for-sell", action="store_true")
    parser.add_argument("--run-eod-reconciliation", action="store_true")
    parser.add_argument("--broker-statement-dir")
    parser.add_argument("--broker-statement-schema", choices=["generic_broker_statement", "qmt_statement_skeleton"], default="generic_broker_statement")
    parser.add_argument("--broker-statement-schema-config")
    parser.add_argument("--eod-reconciliation-dir")
    parser.add_argument("--fail-on-reconciliation-error", action="store_true")
    parser.add_argument("--create-adjustment-proposals", action="store_true")
    parser.add_argument("--create-adjustment-approval", action="store_true")
    parser.add_argument("--apply-approved-adjustments", action="store_true")
    parser.add_argument("--adjustment-approval-id")
    parser.add_argument("--risk-controls", action="store_true")
    parser.add_argument("--risk-policy-path")
    parser.add_argument("--risk-control-state-dir")
    parser.add_argument("--risk-control-output-dir")
    parser.add_argument("--risk-policy-profile", default="cn_ashare_paper_default")
    parser.add_argument("--risk-fail-on-breach", action="store_true")
    parser.add_argument("--risk-allow-clipping", action="store_true")
    parser.add_argument("--create-risk-override-approval", action="store_true")
    parser.add_argument("--risk-override-approval-store-dir")
    parser.add_argument("--risk-override-approval-id")
    parser.add_argument("--block-on-kill-switch", action="store_true")
    parser.add_argument("--force-risk-local-override", action="store_true")
    parser.add_argument("--production-run-id")
    parser.add_argument("--production-state-dir")
    parser.add_argument("--run-mode", choices=["shadow_only", "paper_simulated", "file_outbox", "dry_run"])
    parser.add_argument("--orchestrator-artifact-dir")
    parser.add_argument("--shadow-dir")
    parser.add_argument("--reconcile-only", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    runner = ProductionDailyRunner(
        data_dir=args.data_dir,
        factor_store_dir=args.factor_store_dir,
        approval_store_dir=args.approval_store_dir,
        paper_account_dir=args.paper_account_dir,
        output_dir=args.output_dir,
        orders_dir=args.orders_dir,
        factor_id=args.factor_id,
        latest_production=args.latest_production,
        rebalance_date=args.rebalance_date,
        portfolio_method=args.portfolio_method,
        portfolio_policy_path=args.portfolio_policy_path,
        portfolio_certification_decision_path=args.portfolio_certification_decision_path,
        certified_portfolio_policy_path=args.certified_portfolio_policy_path,
        require_certified_portfolio_policy=args.require_certified_portfolio_policy,
        active_optimizer_policy=args.active_optimizer_policy,
        require_active_optimizer_policy=args.require_active_optimizer_policy,
        portfolio_policy_model_version_id=args.portfolio_policy_model_version_id,
        index_code=args.index_code,
        top_n=args.top_n,
        max_weight=args.max_weight,
        portfolio_value=args.portfolio_value,
        use_factor_risk_model=args.use_factor_risk_model,
        risk_model_lookback=args.risk_model_lookback,
        risk_model_shrinkage=args.risk_model_shrinkage,
        max_style_exposure=args.max_style_exposure,
        max_active_style_exposure=args.max_active_style_exposure,
        capacity_aware=args.capacity_aware,
        execution_plan_dir=args.execution_plan_dir,
        max_participation=args.max_participation,
        execution_buckets=args.execution_buckets,
        broker_adapter=args.broker_adapter,
        broker_store_dir=args.broker_store_dir,
        broker_outbox_dir=args.broker_outbox_dir,
        broker_inbox_dir=args.broker_inbox_dir,
        broker_auto_fill=args.broker_auto_fill,
        broker_reconcile=args.broker_reconcile,
        broker_price_type=args.broker_price_type,
        broker_file_gateway=args.broker_file_gateway,
        broker_file_profile=args.broker_file_profile,
        broker_file_profile_config=args.broker_file_profile_config,
        broker_file_gateway_store_dir=args.broker_file_gateway_store_dir,
        broker_file_outbox_dir=args.broker_file_outbox_dir,
        broker_file_inbox_dir=args.broker_file_inbox_dir,
        broker_file_handoff_dir=args.broker_file_handoff_dir,
        operator_handoff_store_dir=args.operator_handoff_store_dir,
        operator_handoff_approval_store_dir=args.operator_handoff_approval_store_dir,
        mapping_certification_decision_path=args.mapping_certification_decision_path,
        require_mapping_certification=args.require_mapping_certification,
        file_outbox_dry_run=args.file_outbox_dry_run,
        auto_confirm_local_smoke=args.auto_confirm_local_smoke,
        use_model_registry=args.use_model_registry,
        model_registry_dir=args.model_registry_dir,
        model_version_id=args.model_version_id,
        require_active_model=args.require_active_model,
        allow_production_candidate_fallback=args.allow_production_candidate_fallback,
        model_kind=args.model_kind,
        model_environment=args.model_environment,
        block_paused_model=args.block_paused_model,
        block_quarantined_model=args.block_quarantined_model,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        min_listing_days=args.min_listing_days,
        exclude_st=args.exclude_st,
        corporate_action_aware=args.corporate_action_aware,
        apply_corporate_actions=args.apply_corporate_actions,
        corporate_action_dir=args.corporate_action_dir,
        corporate_action_output_dir=args.corporate_action_output_dir,
        target_return_mode=args.target_return_mode,
        corporate_action_application_date_mode=args.corporate_action_application_date_mode,
        corporate_action_cash_field=args.corporate_action_cash_field,
        reconcile_adjustment_factors=args.reconcile_adjustment_factors,
        settlement_aware=args.settlement_aware,
        settlement_dir=args.settlement_dir,
        settlement_profile=args.settlement_profile,
        cost_basis_method=args.cost_basis_method,
        settle_before_trading=args.settle_before_trading,
        settle_through_date=args.settle_through_date,
        enforce_available_cash=args.enforce_available_cash,
        enforce_available_shares=args.enforce_available_shares,
        allow_unsettled_cash_for_buy=args.allow_unsettled_cash_for_buy,
        allow_unsettled_shares_for_sell=args.allow_unsettled_shares_for_sell,
        run_eod_reconciliation=args.run_eod_reconciliation,
        broker_statement_dir=args.broker_statement_dir,
        broker_statement_schema=args.broker_statement_schema,
        broker_statement_schema_config=args.broker_statement_schema_config,
        eod_reconciliation_dir=args.eod_reconciliation_dir,
        fail_on_reconciliation_error=args.fail_on_reconciliation_error,
        create_adjustment_proposals=args.create_adjustment_proposals,
        create_adjustment_approval=args.create_adjustment_approval,
        apply_approved_adjustments=args.apply_approved_adjustments,
        adjustment_approval_id=args.adjustment_approval_id,
        risk_controls=args.risk_controls,
        risk_policy_path=args.risk_policy_path,
        risk_control_state_dir=args.risk_control_state_dir,
        risk_control_output_dir=args.risk_control_output_dir,
        risk_policy_profile=args.risk_policy_profile,
        risk_fail_on_breach=args.risk_fail_on_breach,
        risk_allow_clipping=args.risk_allow_clipping,
        create_risk_override_approval=args.create_risk_override_approval,
        risk_override_approval_store_dir=args.risk_override_approval_store_dir,
        risk_override_approval_id=args.risk_override_approval_id,
        block_on_kill_switch=args.block_on_kill_switch,
        force_risk_local_override=args.force_risk_local_override,
        production_run_id=args.production_run_id,
        production_state_dir=args.production_state_dir,
        run_mode=args.run_mode,
        orchestrator_artifact_dir=args.orchestrator_artifact_dir,
        shadow_dir=args.shadow_dir,
        data_freeze_dir=args.data_freeze_dir,
        data_freeze_id=args.data_freeze_id,
        data_version_manifest_path=args.data_version_manifest_path,
        require_data_freeze=args.require_data_freeze,
        freeze_validation_report_path=args.freeze_validation_report_path,
    )
    result = runner.run(
        require_approval=args.require_approval,
        approval_id=args.approval_id,
        execute_approved=args.execute_approved,
        reconcile_only=args.reconcile_only,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
