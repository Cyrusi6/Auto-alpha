"""CLI for multi-day production replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .calendar import build_replay_trade_dates
from .models import ProductionReplayConfig, ReplayMode
from .planner import make_replay_id
from .runner import ProductionReplayRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run multi-day local production replay.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["plan", "run", "resume", "aggregate", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--replay-id")
    parser.add_argument("--replay-name", default="local_replay")
    parser.add_argument("--replay-mode", choices=[ReplayMode.shadow_only, ReplayMode.paper_simulated, ReplayMode.file_outbox_dry_run, ReplayMode.mixed], default=ReplayMode.shadow_only)
    parser.add_argument("--replay-state-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--trade-dates")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--require-active-model", action="store_true")
    parser.add_argument("--require-active-optimizer-policy", action="store_true")
    parser.add_argument("--certified-portfolio-policy-path")
    parser.add_argument("--portfolio-certification-decision-path")
    parser.add_argument("--require-certified-portfolio-policy", action="store_true")
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--paper-account-dir")
    parser.add_argument("--orders-root-dir")
    parser.add_argument("--shadow-root-dir")
    parser.add_argument("--settlement-dir")
    parser.add_argument("--broker-store-dir")
    parser.add_argument("--broker-adapter", choices=["simulated", "paper", "file"], default="paper")
    parser.add_argument("--broker-file-gateway", action="store_true")
    parser.add_argument("--broker-file-profile", default="generic_broker_csv")
    parser.add_argument("--broker-file-profile-config")
    parser.add_argument("--broker-file-gateway-store-dir")
    parser.add_argument("--broker-file-outbox-root-dir", dest="broker_file_outbox_root_dir")
    parser.add_argument("--broker-file-outbox-dir", dest="broker_file_outbox_root_dir")
    parser.add_argument("--broker-file-inbox-root-dir", dest="broker_file_inbox_root_dir")
    parser.add_argument("--broker-file-inbox-dir", dest="broker_file_inbox_root_dir")
    parser.add_argument("--broker-file-handoff-root-dir", dest="broker_file_handoff_root_dir")
    parser.add_argument("--broker-file-handoff-dir", dest="broker_file_handoff_root_dir")
    parser.add_argument("--operator-handoff-store-dir")
    parser.add_argument("--operator-handoff-approval-store-dir")
    parser.add_argument("--mapping-certification-decision-path")
    parser.add_argument("--require-mapping-certification", action="store_true")
    parser.add_argument("--file-outbox-dry-run", action="store_true")
    parser.add_argument("--auto-confirm-local-smoke", action="store_true")
    parser.add_argument("--risk-control-state-dir")
    parser.add_argument("--risk-control-output-root")
    parser.add_argument("--incident-store-dir")
    parser.add_argument("--monitoring-root-dir")
    parser.add_argument("--portfolio-value", type=float, default=1_000_000.0)
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--max-weight", type=float, default=0.10)
    parser.add_argument("--capacity-aware", action="store_true")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="same_day_after_close")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument("--apply-corporate-actions", action="store_true")
    parser.add_argument("--corporate-action-dir")
    parser.add_argument("--target-return-mode", default="adjusted_close")
    parser.add_argument("--settlement-aware", action="store_true")
    parser.add_argument("--risk-controls", action="store_true")
    parser.add_argument("--auto-approve-paper-local", action="store_true")
    parser.add_argument("--paper-local-reviewer", default="local_replay_reviewer")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-on-blocker", action="store_true")
    parser.add_argument("--continue-on-warning", action="store_true")
    parser.add_argument("--max-failed-days", type=int, default=0)
    parser.add_argument("--strict-calendar", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    trade_dates = _parse_dates(args.trade_dates)
    if args.command == "smoke" and not trade_dates:
        trade_dates = [args.start_date]
    trade_dates = build_replay_trade_dates(args.data_dir, args.start_date, args.end_date, trade_dates, args.strict_calendar)
    replay_id = args.replay_id or make_replay_id(args.replay_name, args.start_date, args.end_date, args.replay_mode)
    cfg = ProductionReplayConfig(
        replay_id=replay_id,
        replay_name=args.replay_name,
        replay_mode=args.replay_mode,
        start_date=args.start_date,
        end_date=args.end_date,
        trade_dates=trade_dates,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        replay_state_dir=args.replay_state_dir,
        factor_store_dir=args.factor_store_dir,
        model_registry_dir=args.model_registry_dir,
        approval_store_dir=args.approval_store_dir,
        paper_account_dir=args.paper_account_dir,
        orders_root_dir=args.orders_root_dir,
        shadow_root_dir=args.shadow_root_dir,
        settlement_dir=args.settlement_dir,
        broker_store_dir=args.broker_store_dir,
        broker_adapter=args.broker_adapter,
        broker_file_gateway=args.broker_file_gateway,
        broker_file_profile=args.broker_file_profile,
        broker_file_profile_config=args.broker_file_profile_config,
        broker_file_gateway_store_dir=args.broker_file_gateway_store_dir,
        broker_file_outbox_root_dir=args.broker_file_outbox_root_dir,
        broker_file_inbox_root_dir=args.broker_file_inbox_root_dir,
        broker_file_handoff_root_dir=args.broker_file_handoff_root_dir,
        operator_handoff_store_dir=args.operator_handoff_store_dir,
        operator_handoff_approval_store_dir=args.operator_handoff_approval_store_dir,
        mapping_certification_decision_path=args.mapping_certification_decision_path,
        require_mapping_certification=args.require_mapping_certification,
        file_outbox_dry_run=args.file_outbox_dry_run,
        auto_confirm_local_smoke=args.auto_confirm_local_smoke,
        monitoring_root_dir=args.monitoring_root_dir,
        incident_store_dir=args.incident_store_dir,
        risk_control_state_dir=args.risk_control_state_dir,
        risk_control_output_root=args.risk_control_output_root,
        portfolio_value=args.portfolio_value,
        index_code=args.index_code,
        top_n=args.top_n,
        max_weight=args.max_weight,
        capacity_aware=args.capacity_aware,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        corporate_action_aware=args.corporate_action_aware,
        apply_corporate_actions=args.apply_corporate_actions,
        corporate_action_dir=args.corporate_action_dir,
        target_return_mode=args.target_return_mode,
        settlement_aware=args.settlement_aware,
        risk_controls=args.risk_controls,
        data_freeze_dir=args.data_freeze_dir,
        data_version_manifest_path=args.data_version_manifest_path,
        require_data_freeze=args.require_data_freeze,
        require_active_model=args.require_active_model,
        require_active_optimizer_policy=args.require_active_optimizer_policy,
        certified_portfolio_policy_path=args.certified_portfolio_policy_path,
        portfolio_certification_decision_path=args.portfolio_certification_decision_path,
        require_certified_portfolio_policy=args.require_certified_portfolio_policy,
        auto_approve_paper_local=args.auto_approve_paper_local,
        paper_local_reviewer=args.paper_local_reviewer,
        stop_on_blocker=args.stop_on_blocker,
        continue_on_warning=args.continue_on_warning,
        max_failed_days=args.max_failed_days,
        strict_calendar=args.strict_calendar,
    )
    runner = ProductionReplayRunner(cfg)
    if args.command == "plan":
        payload = runner.plan()
    elif args.command in {"run", "smoke"}:
        payload = runner.run(resume=args.resume)
    elif args.command == "resume":
        payload = runner.run(resume=True)
    elif args.command in {"aggregate", "report"}:
        payload = runner.aggregate()
    else:  # pragma: no cover
        payload = {"status": "failed", "error": f"unsupported command: {args.command}"}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 1 if payload.get("status") == "failed" else 0


def _parse_dates(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
