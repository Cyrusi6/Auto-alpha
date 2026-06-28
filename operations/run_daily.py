"""CLI for daily local production runs."""

from __future__ import annotations

import argparse
import json

from .daily_runner import ProductionDailyRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local daily production workflow.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--approval-store-dir", required=True)
    parser.add_argument("--paper-account-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--orders-dir", required=True)
    parser.add_argument("--factor-id")
    parser.add_argument("--latest-production", action="store_true")
    parser.add_argument("--rebalance-date")
    parser.add_argument("--portfolio-method", choices=["equal_weight", "risk_aware"], default="equal_weight")
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
    parser.add_argument("--execution-buckets", default="open,morning,afternoon,close")
    parser.add_argument("--broker-adapter", choices=["paper", "simulated", "file"], default="paper")
    parser.add_argument("--broker-store-dir")
    parser.add_argument("--broker-outbox-dir")
    parser.add_argument("--broker-inbox-dir")
    parser.add_argument("--broker-auto-fill", action="store_true", default=True)
    parser.add_argument("--broker-reconcile", action="store_true")
    parser.add_argument("--broker-price-type", default="MARKET")
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
    )
    result = runner.run(
        require_approval=args.require_approval,
        approval_id=args.approval_id,
        execute_approved=args.execute_approved,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
