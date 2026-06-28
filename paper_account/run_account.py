"""CLI for local paper account ledger."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from .ledger import LocalPaperAccount
from .performance import compute_account_performance


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a local paper account.")
    parser.add_argument("--account-dir", required=True)
    parser.add_argument("--account-id", default="paper_ashare")
    parser.add_argument("--pretty", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    reset = sub.add_parser("reset")
    reset.add_argument("--initial-cash", type=float, required=True)
    reset.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    show = sub.add_parser("show")
    show.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    mtm = sub.add_parser("mark-to-market")
    mtm.add_argument("--trade-date", required=True)
    mtm.add_argument("--prices-json", required=True, help="JSON object of ts_code to price.")
    mtm.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    perf = sub.add_parser("performance")
    perf.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    apply_ca = sub.add_parser("apply-corporate-actions")
    apply_ca.add_argument("--data-dir", required=True)
    apply_ca.add_argument("--corporate-action-dir")
    apply_ca.add_argument("--trade-date", required=True)
    apply_ca.add_argument("--application-date-mode", choices=("ex_date", "pay_date", "div_listdate", "record_date"), default="pay_date")
    apply_ca.add_argument("--cash-field", choices=("cash_div", "cash_div_tax"), default="cash_div")
    apply_ca.add_argument("--apply-statuses", default="实施")
    apply_ca.add_argument("--settlement-aware", action="store_true")
    apply_ca.add_argument("--settlement-dir")
    apply_ca.add_argument(
        "--settlement-profile",
        choices=("cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"),
        default="cn_ashare_paper_default",
    )
    apply_ca.add_argument("--cost-basis-method", choices=("average", "fifo"), default="average")
    apply_ca.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    show_ca = sub.add_parser("show-corporate-actions")
    show_ca.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    recon_ca = sub.add_parser("reconcile-corporate-actions")
    recon_ca.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    apply_fills = sub.add_parser("apply-fills")
    apply_fills.add_argument("--data-dir")
    apply_fills.add_argument("--fills-path", required=True)
    apply_fills.add_argument("--trade-date", required=True)
    apply_fills.add_argument("--prices-json")
    apply_fills.add_argument("--settlement-aware", action="store_true")
    apply_fills.add_argument(
        "--settlement-profile",
        choices=("cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"),
        default="cn_ashare_paper_default",
    )
    apply_fills.add_argument("--cost-basis-method", choices=("average", "fifo"), default="average")
    apply_fills.add_argument("--settlement-dir")
    apply_fills.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    settle = sub.add_parser("settle")
    settle.add_argument("--as-of-date", required=True)
    settle.add_argument("--prices-json")
    settle.add_argument(
        "--settlement-profile",
        choices=("cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"),
        default="cn_ashare_paper_default",
    )
    settle.add_argument("--settlement-dir")
    settle.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    precheck = sub.add_parser("precheck-orders")
    precheck.add_argument("--orders-path", required=True)
    precheck.add_argument("--prices-json")
    precheck.add_argument(
        "--settlement-profile",
        choices=("cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"),
        default="cn_ashare_paper_default",
    )
    precheck.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    show_settlement = sub.add_parser("show-settlement")
    show_settlement.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    show_lots = sub.add_parser("show-lots")
    show_lots.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    nav = sub.add_parser("build-nav")
    nav.add_argument("--data-dir")
    nav.add_argument("--as-of-date", required=True)
    nav.add_argument("--prices-json")
    nav.add_argument("--settlement-dir")
    nav.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    recon = sub.add_parser("reconcile-account")
    recon.add_argument("--as-of-date", required=True)
    recon.add_argument("--settlement-dir")
    recon.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    account = LocalPaperAccount(args.account_dir, account_id=args.account_id)
    try:
        if args.command == "reset":
            payload = account.reset(args.initial_cash).to_dict()
        elif args.command == "show":
            payload = account.load_state().to_dict()
        elif args.command == "mark-to-market":
            payload = account.mark_to_market(json.loads(args.prices_json), args.trade_date).to_dict()
        elif args.command == "performance":
            payload = compute_account_performance(account.load_state())
        elif args.command == "apply-corporate-actions":
            from corporate_actions.models import CorporateActionEvent
            from corporate_actions.normalizer import normalize_corporate_action_records
            from corporate_actions.report import read_jsonl

            event_path = (args.corporate_action_dir and f"{args.corporate_action_dir}/corporate_action_events.jsonl") or None
            if event_path and Path(event_path).exists():
                events = [CorporateActionEvent(**record) for record in read_jsonl(event_path)]
            else:
                records = read_jsonl(f"{args.data_dir}/corporate_actions/records.jsonl")
                statuses = tuple(item.strip() for item in args.apply_statuses.split(",") if item.strip())
                events = normalize_corporate_action_records(records, statuses or ("实施",), cash_field=args.cash_field)
            state, applications = account.apply_corporate_actions(
                events,
                trade_date=args.trade_date,
                mode=args.application_date_mode,
            )
            settlement_paths = {}
            if args.settlement_aware and args.settlement_dir:
                from settlement_engine import SettlementCalendar, apply_settlement_events, build_settlement_events_from_corporate_actions, load_settlement_profile
                from settlement_engine.report import write_settlement_report

                profile = load_settlement_profile(args.settlement_profile, cost_basis_method=args.cost_basis_method)
                calendar = SettlementCalendar.from_data_dir(args.data_dir)
                settlement_events = build_settlement_events_from_corporate_actions(applications, profile=profile, calendar=calendar, account_id=state.account_id)
                state = account.save_state(apply_settlement_events(state, settlement_events, args.trade_date, profile=profile))
                settlement_paths = write_settlement_report(state, args.settlement_dir, args.trade_date, profile_name=profile.profile_name)
            payload = {
                "account_id": state.account_id,
                "cash": state.cash,
                "positions": {key: value.to_dict() for key, value in state.positions.items()},
                "applications": [application.to_dict() for application in applications],
                "applied_corporate_action_count": sum(application.status == "APPLIED" for application in applications),
                "corporate_action_ledger_path": str(account.corporate_action_ledger_path),
                "settlement_ledger_path": str(account.settlement_ledger_path),
                "settlement_paths": settlement_paths,
            }
        elif args.command == "show-corporate-actions":
            state = account.load_state()
            payload = {
                "corporate_action_ledger": [entry.to_dict() for entry in state.corporate_action_ledger],
                "settlement_ledger": state.settlement_ledger,
            }
        elif args.command == "reconcile-corporate-actions":
            state = account.load_state()
            ids = [entry.metadata.get("application_id") for entry in state.corporate_action_ledger]
            payload = {
                "ledger_entries": len(state.corporate_action_ledger),
                "duplicate_application_ids": len(ids) - len(set(ids)),
                "cash_ledger_entries": sum(entry.reason == "corporate_action_cash_dividend" for entry in state.cash_ledger),
            }
        elif args.command == "apply-fills":
            from corporate_actions.report import read_jsonl

            fills = read_jsonl(args.fills_path)
            prices = json.loads(args.prices_json) if args.prices_json else None
            if args.settlement_aware:
                if not args.data_dir:
                    raise ValueError("--data-dir is required for settlement-aware apply-fills")
                state = account.apply_fills_settlement_aware(
                    fills,
                    data_dir=args.data_dir,
                    trade_date=args.trade_date,
                    profile=args.settlement_profile,
                    prices=prices,
                    cost_basis_method=args.cost_basis_method,
                )
            else:
                state = account.apply_fills(fills, prices=prices, trade_date=args.trade_date)
            settlement_paths = {}
            if args.settlement_dir:
                from settlement_engine.report import write_settlement_report

                settlement_paths = write_settlement_report(state, args.settlement_dir, args.trade_date, profile_name=args.settlement_profile)
            payload = {"account_id": state.account_id, "cash": state.cash, "positions": len(state.positions), "settlement_paths": settlement_paths}
        elif args.command == "settle":
            prices = json.loads(args.prices_json) if args.prices_json else None
            state = account.settle(args.as_of_date, prices=prices, profile=args.settlement_profile)
            settlement_paths = {}
            if args.settlement_dir:
                from settlement_engine.report import write_settlement_report

                settlement_paths = write_settlement_report(state, args.settlement_dir, args.as_of_date, profile_name=args.settlement_profile)
            payload = {"account_id": state.account_id, "cash": state.cash, "pending_events": sum(event.get("status") == "pending" for event in state.settlement_events), "settlement_paths": settlement_paths}
        elif args.command == "precheck-orders":
            from corporate_actions.report import read_jsonl

            prices = json.loads(args.prices_json) if args.prices_json else None
            payload = account.precheck_orders(read_jsonl(args.orders_path), prices=prices, profile=args.settlement_profile)
        elif args.command == "show-settlement":
            state = account.load_state()
            payload = {
                "cash": state.cash,
                "available_cash": state.available_cash,
                "withdrawable_cash": state.withdrawable_cash,
                "frozen_cash": state.frozen_cash,
                "unsettled_receivable": state.unsettled_receivable,
                "unsettled_payable": state.unsettled_payable,
                "settlement_events": state.settlement_events,
                "position_lots": state.position_lots,
                "realized_pnl_ledger": state.realized_pnl_ledger,
                "account_nav": state.account_nav,
            }
        elif args.command == "show-lots":
            payload = {"position_lots": account.load_state().position_lots}
        elif args.command == "build-nav":
            from settlement_engine.performance import build_account_nav_series
            from settlement_engine.report import write_settlement_report

            prices = json.loads(args.prices_json) if args.prices_json else _load_prices_from_data_dir(args.data_dir, args.as_of_date)
            state = account.load_state()
            nav = build_account_nav_series(state, prices_by_date={args.as_of_date: prices})
            state = account.save_state(replace(state, account_nav=[record.to_dict() for record in nav]))
            settlement_paths = write_settlement_report(state, args.settlement_dir, args.as_of_date) if args.settlement_dir else {}
            payload = {"nav_records": [record.to_dict() for record in nav], "settlement_paths": settlement_paths}
        elif args.command == "reconcile-account":
            report = account.reconcile(args.as_of_date)
            settlement_paths = {}
            if args.settlement_dir:
                from settlement_engine.report import write_settlement_report

                settlement_paths = write_settlement_report(account.load_state(), args.settlement_dir, args.as_of_date)
            payload = report | {"settlement_paths": settlement_paths}
        else:  # pragma: no cover
            raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _load_prices_from_data_dir(data_dir: str | None, trade_date: str) -> dict[str, float]:
    if not data_dir:
        return {}
    from corporate_actions.report import read_jsonl

    path = Path(data_dir) / "daily_bars" / "records.jsonl"
    if not path.exists():
        return {}
    return {
        str(record.get("ts_code")): float(record.get("close") or 0.0)
        for record in read_jsonl(path)
        if str(record.get("trade_date")) == trade_date
    }


if __name__ == "__main__":
    raise SystemExit(main())
