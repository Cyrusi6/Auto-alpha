"""CLI for local paper account ledger."""

from __future__ import annotations

import argparse
import json
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
    apply_ca.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    show_ca = sub.add_parser("show-corporate-actions")
    show_ca.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    recon_ca = sub.add_parser("reconcile-corporate-actions")
    recon_ca.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)
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
            payload = {
                "account_id": state.account_id,
                "cash": state.cash,
                "positions": {key: value.to_dict() for key, value in state.positions.items()},
                "applications": [application.to_dict() for application in applications],
                "applied_corporate_action_count": sum(application.status == "APPLIED" for application in applications),
                "corporate_action_ledger_path": str(account.corporate_action_ledger_path),
                "settlement_ledger_path": str(account.settlement_ledger_path),
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
        else:  # pragma: no cover
            raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
