"""CLI for running local A-share portfolio simulation."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from dataclasses import asdict
from pathlib import Path

from corporate_actions.models import CorporateActionEvent
from corporate_actions.normalizer import normalize_corporate_action_records
from corporate_actions.report import read_jsonl, write_corporate_action_report
from factor_store import LocalFactorStore
from model_core.data_loader import AShareDataLoader
from capacity_model import write_capacity_report
from execution_plan import write_execution_plan_report
from portfolio_optimizer import load_portfolio_policy, portfolio_policy_from_payload, validate_certified_portfolio_policy
from risk_model import write_risk_model_report, write_risk_report
from risk_controls import evaluate_order_records
from data_lake import validate_research_input
from validation_lab.report import write_stress_backtest_artifacts
from validation_lab.stress_backtest import run_stress_backtest_bundle

from .io import describe_factor, factor_values_to_matrix, select_factor_id
from .simulator import AShareBacktestSimulator
from .time_contract import BacktestTimeContract, normalize_execution_mode


def _write_jsonl(path: Path, records: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _write_dict_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local A-share portfolio simulation.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-freeze-id")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--freeze-validation-report-path")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--factor-id")
    parser.add_argument("--latest-approved", action="store_true")
    parser.add_argument("--factor-type", choices=["single", "composite", "any"], default="any")
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--max-weight", type=float, default=0.10)
    parser.add_argument("--portfolio-method", choices=["equal_weight", "risk_aware"], default="equal_weight")
    parser.add_argument("--portfolio-policy-path")
    parser.add_argument("--portfolio-policy-id")
    parser.add_argument("--require-certified-portfolio-policy", action="store_true")
    parser.add_argument("--portfolio-certification-decision-path")
    parser.add_argument("--active-optimizer-policy", action="store_true")
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--risk-aversion", type=float, default=1.0)
    parser.add_argument("--turnover-penalty", type=float, default=0.1)
    parser.add_argument("--max-turnover", type=float, default=1.0)
    parser.add_argument("--max-industry-active-weight", type=float, default=0.20)
    parser.add_argument("--max-tracking-error", type=float, default=1.0)
    parser.add_argument("--risk-report-dir")
    parser.add_argument("--use-factor-risk-model", action="store_true")
    parser.add_argument("--risk-model-lookback", type=int)
    parser.add_argument("--risk-model-shrinkage", type=float, default=0.1)
    parser.add_argument("--attribution", action="store_true")
    parser.add_argument("--max-style-exposure", type=float)
    parser.add_argument("--max-active-style-exposure", type=float)
    parser.add_argument("--max-factor-risk-contribution", type=float)
    parser.add_argument("--capacity-aware", action="store_true")
    parser.add_argument("--capacity-lookback", type=int, default=20)
    parser.add_argument("--max-participation", type=float, default=0.10)
    parser.add_argument("--impact-base-bps", type=float, default=5.0)
    parser.add_argument("--impact-power", type=float, default=0.5)
    parser.add_argument("--execution-buckets", default="open")
    parser.add_argument("--execution-plan-dir")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="next_trade_day_open")
    parser.add_argument("--signal-lag-days", type=int, default=1)
    parser.add_argument("--min-listing-days", type=int, default=0)
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--active-mask-path")
    parser.add_argument("--run-leakage-audit", action="store_true")
    parser.add_argument("--leakage-audit-dir")
    parser.add_argument("--fail-on-leakage-blocker", action="store_true")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument("--corporate-action-dir")
    parser.add_argument(
        "--target-return-mode",
        choices=["adjusted_close", "raw_close", "corporate_action_total_return"],
        default="adjusted_close",
    )
    parser.add_argument("--apply-corporate-actions", action="store_true")
    parser.add_argument("--corporate-action-application-date-mode", default="pay_date")
    parser.add_argument("--corporate-action-report-dir")
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
    parser.add_argument("--enforce-available-cash", action="store_true")
    parser.add_argument("--enforce-available-shares", action="store_true")
    parser.add_argument("--allow-unsettled-cash-for-buy", action="store_true")
    parser.add_argument("--allow-unsettled-shares-for-sell", action="store_true")
    parser.add_argument("--settle-through-date")
    parser.add_argument("--write-settlement-report", action="store_true")
    parser.add_argument("--risk-controls", action="store_true")
    parser.add_argument("--risk-policy-path")
    parser.add_argument("--risk-policy-profile", default="cn_ashare_paper_default")
    parser.add_argument("--risk-control-dir")
    parser.add_argument("--risk-fail-on-breach", action="store_true")
    parser.add_argument("--risk-allow-clipping", action="store_true")
    parser.add_argument("--risk-state-reset-each-run", action="store_true")
    parser.add_argument("--validation-bundle", action="store_true")
    parser.add_argument("--validation-output-dir")
    parser.add_argument("--stress-cost-multipliers", default="1.0,2.0")
    parser.add_argument("--stress-participations", default="0.10,0.05")
    parser.add_argument("--stress-settlement-profiles", default="cn_ashare_paper_default,conservative_t_plus_one_cash")
    parser.add_argument("--stress-top-n-values", default="")
    parser.add_argument("--stress-max-weight-values", default="")
    parser.add_argument("--write-validation-stress-report", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.feature_cutoff_mode == "same_day_after_close" and int(args.signal_lag_days) <= 0:
        print(json.dumps({"status": "blocked", "error": "same_day_after_close with signal_lag_days=0 is look-ahead leakage"}, ensure_ascii=False))
        return 1
    try:
        execution_mode, timing_warnings = normalize_execution_mode(args.feature_cutoff_mode)
    except ValueError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 1
    if int(args.signal_lag_days) < 0:
        print(json.dumps({"status": "blocked", "error": "signal_lag_days must be non-negative"}, ensure_ascii=False))
        return 1
    output_dir = Path(args.output_dir)
    freeze_report = validate_research_input(args.data_dir, args.data_freeze_dir, args.require_data_freeze)
    if freeze_report.error_count > 0:
        print(json.dumps({"error": "data freeze validation failed", "freeze_validation_status": freeze_report.status}, ensure_ascii=False))
        return 1
    if args.data_freeze_dir:
        args.data_dir = str(Path(args.data_freeze_dir) / "data")
    portfolio_policy, policy_gate = _resolve_portfolio_policy(args)
    if policy_gate.get("blocked"):
        print(json.dumps({"error": "portfolio policy certification gate failed", "portfolio_policy_gate": policy_gate}, ensure_ascii=False))
        return 1
    loader = AShareDataLoader(
        data_dir=args.data_dir,
        device="cpu",
        universe_file=args.universe_file,
        universe_name=args.universe_name,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        min_listing_days=args.min_listing_days,
        exclude_st=args.exclude_st,
        active_security_mask_path=args.active_mask_path,
        corporate_action_aware=args.corporate_action_aware,
        corporate_action_dir=args.corporate_action_dir,
        target_return_mode=args.target_return_mode,
        corporate_action_cash_field=args.corporate_action_cash_field,
        corporate_action_application_mode=args.corporate_action_application_date_mode,
    ).load_data()
    store = LocalFactorStore(args.factor_store_dir)
    factor_id = select_factor_id(
        store,
        args.factor_id,
        latest_approved=args.latest_approved,
        factor_type=args.factor_type,
    )
    factor_meta = describe_factor(store, factor_id)
    values = store.load_factor_values(factor_id)
    factors = factor_values_to_matrix(values, loader.ts_codes, loader.trade_dates)
    factors = apply_signal_lag(factors, int(args.signal_lag_days))
    policy_context = _portfolio_policy_context(portfolio_policy, policy_gate)

    simulator = AShareBacktestSimulator(
        initial_cash=args.initial_cash,
        top_n=args.top_n,
        max_weight=args.max_weight,
        portfolio_method=args.portfolio_method,
        index_code=args.index_code,
        risk_aversion=args.risk_aversion,
        turnover_penalty=args.turnover_penalty,
        max_turnover=args.max_turnover,
        max_industry_active_weight=args.max_industry_active_weight,
        max_tracking_error=args.max_tracking_error,
        factor_id=factor_id,
        use_factor_risk_model=args.use_factor_risk_model,
        risk_model_lookback=args.risk_model_lookback,
        risk_model_shrinkage=args.risk_model_shrinkage,
        attribution=args.attribution,
        max_style_exposure=args.max_style_exposure,
        max_active_style_exposure=args.max_active_style_exposure,
        max_factor_risk_contribution=args.max_factor_risk_contribution,
        capacity_aware=args.capacity_aware,
        capacity_lookback=args.capacity_lookback,
        max_participation=args.max_participation,
        impact_base_bps=args.impact_base_bps,
        impact_power=args.impact_power,
        execution_buckets=tuple(item.strip() for item in args.execution_buckets.split(",") if item.strip()),
        time_contract=BacktestTimeContract(signal_lag_days=int(args.signal_lag_days)),
    )
    result = simulator.simulate(factors, loader)
    result.metrics["data_freeze_enabled"] = 1.0 if args.data_freeze_dir else 0.0
    result.metrics["data_hash_drift_count"] = float(freeze_report.error_count)
    result.metrics["signal_lag_days"] = float(args.signal_lag_days)
    result.metrics["execution_timing_mode"] = execution_mode
    result.metrics["execution_timing_warnings"] = timing_warnings
    if args.point_in_time and "active_mask" in loader.raw_data_cache:
        active_mask = loader.raw_data_cache["active_mask"]
        result.metrics["active_universe_coverage"] = float(active_mask.mean().item()) if active_mask.numel() else 0.0
    corporate_paths: dict[str, str | None] = {
        "corporate_action_report_path": None,
        "total_return_report_path": None,
        "adjustment_reconciliation_path": None,
    }
    if args.corporate_action_aware or args.corporate_action_report_dir:
        corporate_paths, corporate_summary = _write_corporate_action_artifacts(args, loader)
        result.metrics.update(
            {
                "corporate_action_event_count": float(corporate_summary.get("event_count", 0) or 0),
                "implemented_action_count": float(corporate_summary.get("implemented_action_count", 0) or 0),
                "cash_dividend_amount": float(corporate_summary.get("cash_dividend_amount_per_share", 0.0) or 0.0),
                "stock_distribution_event_count": float(corporate_summary.get("stock_distribution_event_count", 0) or 0),
                "corporate_action_warning_count": float(corporate_summary.get("corporate_action_warning_count", 0) or 0),
                "corporate_action_error_count": float(corporate_summary.get("corporate_action_error_count", 0) or 0),
                "adjustment_reconciliation_warning_count": float(
                    corporate_summary.get("adjustment_reconciliation_warning_count", 0) or 0
                ),
                "adjustment_reconciliation_error_count": float(
                    corporate_summary.get("adjustment_reconciliation_error_count", 0) or 0
                ),
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "backtest_result.json").write_text(
        json.dumps(_backtest_payload(result, policy_context), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_jsonl(output_dir / "equity_curve.jsonl", result.snapshots)
    _write_jsonl(output_dir / "trades.jsonl", result.fills)
    risk_control_paths: dict[str, str | None] = {
        "risk_control_report_path": None,
        "risk_control_breaches_path": None,
        "risk_limit_usage_path": None,
        "risk_control_decisions_path": None,
        "accepted_orders_path": None,
        "rejected_orders_path": None,
        "clipped_orders_path": None,
        "kill_switch_state_path": None,
    }
    risk_control_summary: dict[str, object] = {"status": "not_run"}
    if args.risk_controls:
        risk_dir = Path(args.risk_control_dir) if args.risk_control_dir else output_dir / "risk_controls"
        state_dir = risk_dir / "state"
        if args.risk_state_reset_each_run and state_dir.exists():
            import shutil

            shutil.rmtree(state_dir)
        report, _split, paths = evaluate_order_records(
            result.fills,
            policy_path=args.risk_policy_path,
            policy_profile=args.risk_policy_profile,
            state_dir=state_dir,
            output_dir=risk_dir,
            batch_id=f"backtest_{factor_id}",
            trade_date=loader.trade_dates[-1] if loader.trade_dates else "",
            scope="order",
            allow_clipping=args.risk_allow_clipping,
        )
        risk_control_paths = {key: str(value) for key, value in paths.items()}
        risk_control_summary = {
            "status": report.status,
            "accepted_orders": report.accepted_orders,
            "rejected_orders": report.rejected_orders,
            "clipped_orders": report.clipped_orders,
            "warning_count": report.warning_count,
            "error_count": report.error_count,
            "blocker_count": report.blocker_count,
        }
        result.metrics.update(
            {
                "risk_control_rejected_orders": float(report.rejected_orders),
                "risk_control_clipped_orders": float(report.clipped_orders),
                "risk_control_warning_count": float(report.warning_count),
                "risk_control_error_count": float(report.error_count),
            }
        )
        (output_dir / "backtest_result.json").write_text(json.dumps(_backtest_payload(result, policy_context), ensure_ascii=False, indent=2), encoding="utf-8")
        if args.risk_fail_on_breach and report.rejected_orders > 0:
            print(json.dumps({"error": "risk controls rejected backtest orders", **risk_control_summary}, ensure_ascii=False))
            return 1
    risk_exposures_path = None
    risk_decomposition_path = None
    return_attribution_path = None
    if simulator.risk_exposure_rows:
        risk_exposures_path = output_dir / "risk_exposures.jsonl"
        _write_dict_jsonl(risk_exposures_path, simulator.risk_exposure_rows)
    if simulator.risk_decomposition_rows:
        risk_decomposition_path = output_dir / "risk_decomposition.jsonl"
        _write_dict_jsonl(risk_decomposition_path, simulator.risk_decomposition_rows)
    if simulator.return_attribution_rows:
        return_attribution_path = output_dir / "return_attribution.jsonl"
        _write_dict_jsonl(return_attribution_path, simulator.return_attribution_rows)
    risk_report_path = None
    risk_report_md_path = None
    optimization_result_path = None
    if args.portfolio_method == "risk_aware":
        risk_dir = Path(args.risk_report_dir) if args.risk_report_dir else output_dir
        if simulator.risk_reports:
            risk_json, risk_md = write_risk_report(simulator.risk_reports[-1], risk_dir)
            risk_report_path = str(risk_json)
            risk_report_md_path = str(risk_md)
            if args.use_factor_risk_model:
                risk_model_json, risk_model_md = write_risk_model_report(simulator.risk_reports[-1], risk_dir)
                risk_report_path = str(risk_model_json)
                risk_report_md_path = str(risk_model_md)
        if simulator.optimization_results:
            optimization_result_path = output_dir / "optimization_result.json"
            optimization_result_path.write_text(
                json.dumps(
                    {
                        "factor_id": factor_id,
                        "factor_type": factor_meta["factor_type"],
                        "component_factor_ids": factor_meta["component_factor_ids"],
                        "portfolio_method": args.portfolio_method,
                        "index_code": args.index_code,
                        "latest": simulator.optimization_results[-1].to_dict(),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

    capacity_report_path = None
    capacity_report_md_path = None
    execution_plan_paths: dict[str, str | None] = {
        "execution_plan_path": None,
        "execution_plan_md_path": None,
        "parent_orders_path": None,
        "child_orders_path": None,
        "child_fills_path": None,
        "execution_quality_path": None,
    }
    if args.capacity_aware and simulator.execution_plan_results:
        plan_dir = Path(args.execution_plan_dir) if args.execution_plan_dir else output_dir / "execution_plan"
        paths = write_execution_plan_report(simulator.execution_plan_results[-1], plan_dir)
        execution_plan_paths = {key: str(path) for key, path in paths.items()}
        if simulator.capacity_reports:
            capacity_json, capacity_md = write_capacity_report(simulator.capacity_reports[-1], plan_dir)
            capacity_report_path = str(capacity_json)
            capacity_report_md_path = str(capacity_md)

    leakage_paths: dict[str, str | None] = {
        "leakage_audit_report_path": None,
        "truncation_consistency_report_path": None,
    }
    leakage_gate_status = "not_run"
    if args.run_leakage_audit:
        from leakage_audit.run_audit import main as leakage_audit_main

        leakage_dir = Path(args.leakage_audit_dir) if args.leakage_audit_dir else output_dir / "leakage_audit"
        audit_argv = [
            "--data-dir",
            args.data_dir,
            "--factor-store-dir",
            args.factor_store_dir,
            "--factor-id",
            factor_id,
            "--backtest-result-path",
            str(output_dir / "backtest_result.json"),
            "--output-dir",
            str(leakage_dir),
            "--as-of-date",
            loader.trade_dates[-1],
            "--cutoff-date",
            loader.trade_dates[-1],
            "--run-static-scan",
            "--run-truncation-test",
        ]
        if args.point_in_time:
            audit_argv.extend(["--point-in-time", "--feature-cutoff-mode", args.feature_cutoff_mode])
        if args.fail_on_leakage_blocker:
            audit_argv.append("--fail-on-blocker")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            exit_code = leakage_audit_main(audit_argv)
        if exit_code != 0:
            return exit_code
        report_path = leakage_dir / "leakage_audit_report.json"
        leakage_paths = {
            "leakage_audit_report_path": str(report_path),
            "truncation_consistency_report_path": str(leakage_dir / "truncation_consistency_report.json"),
        }
        if report_path.exists():
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            leakage_gate_status = str(payload.get("leakage_gate_status") or payload.get("status") or "unknown")
            result.metrics["leakage_warning_count"] = float(payload.get("warning_count", 0) or 0)
            result.metrics["leakage_blocker_count"] = float(payload.get("blocker_count", 0) or 0)
            (output_dir / "backtest_result.json").write_text(json.dumps(_backtest_payload(result, policy_context), ensure_ascii=False, indent=2), encoding="utf-8")

    settlement_paths: dict[str, str | None] = {
        "settlement_report_path": None,
        "settlement_report_md_path": None,
        "settlement_events_path": None,
        "cash_buckets_path": None,
        "position_lots_path": None,
        "position_availability_path": None,
        "realized_pnl_path": None,
        "account_nav_path": None,
        "account_performance_report_path": None,
        "account_reconciliation_report_path": None,
        "fee_tax_report_path": None,
    }
    if args.settlement_aware or args.write_settlement_report:
        from paper_account import LocalPaperAccount
        from settlement_engine.report import write_settlement_report

        settlement_dir = Path(args.settlement_dir) if args.settlement_dir else output_dir / "settlement"
        account = LocalPaperAccount(settlement_dir / "account")
        if account.load_state().initial_cash <= 0:
            account.reset(args.initial_cash)
        fills_by_date: dict[str, list[object]] = {}
        for fill in result.fills:
            fills_by_date.setdefault(fill.trade_date, []).append(fill)
        for trade_date, fills in sorted(fills_by_date.items()):
            prices = _prices_from_loader(loader, trade_date)
            account.apply_fills_settlement_aware(
                fills,
                data_dir=args.data_dir,
                trade_date=trade_date,
                profile=args.settlement_profile,
                prices=prices,
                cost_basis_method=args.cost_basis_method,
            )
        settle_date = args.settle_through_date or (loader.trade_dates[-1] if loader.trade_dates else "")
        state = account.settle(settle_date, prices=_prices_from_loader(loader, settle_date), profile=args.settlement_profile)
        if settle_date:
            state = account.mark_to_market(_prices_from_loader(loader, settle_date), settle_date)
        settlement_paths = write_settlement_report(state, settlement_dir, settle_date, profile_name=args.settlement_profile)
        reconciliation_payload = {}
        if settlement_paths.get("account_reconciliation_report_path"):
            reconciliation_payload = json.loads(Path(settlement_paths["account_reconciliation_report_path"]).read_text(encoding="utf-8"))
        fee_payload = {}
        if settlement_paths.get("fee_tax_report_path"):
            fee_payload = json.loads(Path(settlement_paths["fee_tax_report_path"]).read_text(encoding="utf-8"))
        result.metrics.update(
            {
                "settlement_aware": 1.0 if args.settlement_aware else 0.0,
                "pending_settlement_events": float(sum(event.get("status") == "pending" for event in state.settlement_events)),
                "failed_settlement_events": float(sum(event.get("status") == "failed" for event in state.settlement_events)),
                "available_cash": float(state.available_cash),
                "available_cash_min": float(state.available_cash),
                "realized_pnl": float(sum(float(record.get("realized_pnl", 0.0) or 0.0) for record in state.realized_pnl_ledger)),
                "unrealized_pnl": float(sum(float(position.unrealized_pnl) for position in state.positions.values())),
                "total_fees": float(fee_payload.get("total_fee_tax", 0.0) or 0.0),
                "total_commission": float(fee_payload.get("commission", 0.0) or 0.0),
                "total_stamp_duty": float(fee_payload.get("stamp_duty", 0.0) or 0.0),
                "total_transfer_fee": float(fee_payload.get("transfer_fee", 0.0) or 0.0),
                "total_slippage": float(fee_payload.get("slippage", 0.0) or 0.0),
                "nav_difference": float(reconciliation_payload.get("nav_difference", 0.0) or 0.0),
                "settlement_reconciliation_error_count": float(reconciliation_payload.get("error_count", 0) or 0),
            }
        )
        (output_dir / "backtest_result.json").write_text(json.dumps(_backtest_payload(result, policy_context), ensure_ascii=False, indent=2), encoding="utf-8")

    validation_paths: dict[str, str | None] = {
        "stress_backtest_report_path": None,
        "stress_backtest_report_md_path": None,
        "stress_backtest_results_path": None,
    }
    validation_summary: dict[str, object] = {"enabled": False}
    if args.validation_bundle or args.write_validation_stress_report:
        validation_dir = Path(args.validation_output_dir) if args.validation_output_dir else output_dir / "validation"
        stress_results, stress_summary = run_stress_backtest_bundle(
            result.metrics,
            cost_multipliers=_parse_float_list(args.stress_cost_multipliers),
            participations=_parse_float_list(args.stress_participations),
            settlement_profiles=_parse_str_list(args.stress_settlement_profiles),
            top_n_values=_parse_int_list(args.stress_top_n_values),
            max_weight_values=_parse_float_list(args.stress_max_weight_values),
        )
        validation_paths = write_stress_backtest_artifacts(validation_dir, stress_results, stress_summary)
        validation_summary = {
            "enabled": True,
            **stress_summary,
            "scenario_count": len(stress_results),
        }
        result.metrics.update(
            {
                "validation_bundle_enabled": 1.0,
                "stress_backtest_pass_ratio": float(stress_summary.get("stress_backtest_pass_ratio", 0.0) or 0.0),
                "stress_scenario_count": float(stress_summary.get("stress_scenario_count", 0) or 0),
            }
        )
        enriched = _backtest_payload(result, policy_context)
        enriched["validation_bundle"] = validation_summary
        enriched["validation_paths"] = validation_paths
        (output_dir / "backtest_result.json").write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "factor_id": factor_id,
        "factor_type": factor_meta["factor_type"],
        "component_factor_ids": factor_meta["component_factor_ids"],
        "portfolio_method": args.portfolio_method,
        "portfolio_policy_id": portfolio_policy.policy_id if portfolio_policy else None,
        "portfolio_policy_path": args.portfolio_policy_path,
        "portfolio_policy_gate": policy_gate,
        "output_dir": str(output_dir),
        "metrics": result.metrics,
        "n_snapshots": len(result.snapshots),
        "n_trades": len(result.fills),
        "risk_report_path": risk_report_path,
        "risk_report_md_path": risk_report_md_path,
        "optimization_result_path": str(optimization_result_path) if optimization_result_path else None,
        "risk_exposures_path": str(risk_exposures_path) if risk_exposures_path else None,
        "risk_decomposition_path": str(risk_decomposition_path) if risk_decomposition_path else None,
        "return_attribution_path": str(return_attribution_path) if return_attribution_path else None,
        "capacity_report_path": capacity_report_path,
        "capacity_report_md_path": capacity_report_md_path,
        "point_in_time": bool(args.point_in_time),
        "feature_cutoff_mode": args.feature_cutoff_mode,
        "signal_lag_days": args.signal_lag_days,
        "corporate_action_aware": bool(args.corporate_action_aware),
        "target_return_mode": args.target_return_mode,
        "settlement_aware": bool(args.settlement_aware),
        "settlement_profile": args.settlement_profile,
        "cost_basis_method": args.cost_basis_method,
        "leakage_gate_status": leakage_gate_status,
        **leakage_paths,
        **corporate_paths,
        **execution_plan_paths,
        **settlement_paths,
        "risk_controls": bool(args.risk_controls),
        "risk_control_summary": risk_control_summary,
        "data_freeze_dir": args.data_freeze_dir,
        "data_freeze_id": args.data_freeze_id or freeze_report.freeze_id,
        "data_freeze_hash": freeze_report.content_hash,
        "freeze_validation_status": freeze_report.status,
        "data_version_manifest_path": args.data_version_manifest_path,
        "freeze_validation_report_path": args.freeze_validation_report_path,
        **risk_control_paths,
        "validation_bundle": validation_summary,
        **validation_paths,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _resolve_portfolio_policy(args: argparse.Namespace):
    policy = None
    if args.active_optimizer_policy:
        if not args.model_registry_dir:
            return None, {"blocked": True, "reason": "model_registry_dir_required_for_active_optimizer_policy"}
        from model_registry import LocalModelRegistry

        active = LocalModelRegistry(args.model_registry_dir).latest_active_optimizer_policy()
        if active is None:
            return None, {"blocked": bool(args.require_certified_portfolio_policy), "reason": "active_optimizer_policy_not_found"}
        source = active.source_artifacts.get("certified_portfolio_policy_path") or active.source_artifacts.get("selected_portfolio_policy_path")
        if source and Path(source).exists():
            policy = load_portfolio_policy(source)
            args.portfolio_policy_path = str(source)
        else:
            policy = portfolio_policy_from_payload(active.metadata.get("portfolio_policy", active.metadata))
    elif args.portfolio_policy_path:
        policy = load_portfolio_policy(args.portfolio_policy_path)

    if policy is not None:
        args.portfolio_method = policy.portfolio_method
        args.index_code = policy.index_code
        args.top_n = policy.top_n
        args.max_weight = policy.max_weight
        args.risk_aversion = policy.risk_aversion
        args.turnover_penalty = policy.turnover_penalty
        args.max_turnover = policy.max_turnover
        args.max_industry_active_weight = policy.max_industry_active_weight
        args.max_tracking_error = policy.max_tracking_error
        args.use_factor_risk_model = policy.use_factor_risk_model
        args.risk_model_lookback = policy.risk_model_lookback
        args.risk_model_shrinkage = policy.risk_model_shrinkage
        args.max_style_exposure = policy.max_style_exposure
        args.max_active_style_exposure = policy.max_active_style_exposure
        args.max_factor_risk_contribution = policy.max_factor_risk_contribution

    gate = validate_certified_portfolio_policy(
        args.portfolio_policy_path,
        args.portfolio_certification_decision_path,
        require=args.require_certified_portfolio_policy,
    ).to_dict()
    if policy is not None and not args.portfolio_policy_path and policy.certification_status in {"certified", "conditional"}:
        gate.update({"certified": True, "status": policy.certification_status, "reasons": []})
    gate["blocked"] = bool(args.require_certified_portfolio_policy and gate.get("reasons"))
    return policy, gate


def _portfolio_policy_context(policy, gate: dict[str, object]) -> dict[str, object]:
    return {"policy": policy.to_dict() if policy is not None else None, "gate": gate}


def _backtest_payload(result, policy_context: dict[str, object]) -> dict[str, object]:
    payload = result.to_dict()
    payload["portfolio_policy"] = policy_context
    return payload


def _write_corporate_action_artifacts(args: argparse.Namespace, loader: AShareDataLoader) -> tuple[dict[str, str | None], dict[str, object]]:
    output_dir = Path(args.corporate_action_report_dir) if args.corporate_action_report_dir else Path(args.output_dir) / "corporate_actions"
    events_path = Path(args.corporate_action_dir) / "corporate_action_events.jsonl" if args.corporate_action_dir else None
    if events_path is not None and events_path.exists():
        events = [CorporateActionEvent(**record) for record in read_jsonl(events_path)]
    else:
        events = normalize_corporate_action_records(
            getattr(loader, "raw_corporate_actions", []),
            cash_field=args.corporate_action_cash_field,
        )
    paths = write_corporate_action_report(
        args.data_dir,
        events,
        output_dir,
        start_date=loader.trade_dates[0] if loader.trade_dates else "00000000",
        end_date=loader.trade_dates[-1] if loader.trade_dates else "99999999",
        total_return_mode="cash_reinvested",
        reconcile_adjustment=args.reconcile_adjustment_factors,
    )
    summary = json.loads(Path(paths["corporate_actions_report_path"]).read_text(encoding="utf-8"))
    return paths, summary


def _prices_from_loader(loader: AShareDataLoader, trade_date: str) -> dict[str, float]:
    if not trade_date or trade_date not in loader.trade_dates:
        return {}
    close = loader.raw_data_cache["close"].detach().cpu()
    date_idx = loader.trade_dates.index(trade_date)
    return {ts_code: float(close[idx, date_idx].item()) for idx, ts_code in enumerate(loader.ts_codes)}


def _parse_float_list(value: str | None) -> list[float]:
    return [float(item.strip()) for item in (value or "").split(",") if item.strip()]


def _parse_int_list(value: str | None) -> list[int]:
    return [int(float(item.strip())) for item in (value or "").split(",") if item.strip()]


def _parse_str_list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def apply_signal_lag(factors, signal_lag_days: int):
    """Move signal availability to the actual target-weight and execution date."""
    import torch

    lag = int(signal_lag_days)
    tensor = factors.detach().clone() if hasattr(factors, "detach") else torch.tensor(factors, dtype=torch.float32)
    if lag == 0:
        return tensor
    shifted = torch.full_like(tensor, float("nan"))
    if lag < tensor.shape[1]:
        shifted[:, lag:] = tensor[:, :-lag]
    return shifted


if __name__ == "__main__":
    raise SystemExit(main())
