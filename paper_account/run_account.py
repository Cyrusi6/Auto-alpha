"""CLI for local paper account ledger."""

from __future__ import annotations

import argparse
import json

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
        else:  # pragma: no cover
            raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
