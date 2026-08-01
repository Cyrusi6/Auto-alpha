"""CLI for local settlement-aware paper accounting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from corporate_actions.report import read_jsonl
from paper_account import LocalPaperAccount

from .calendar import SettlementCalendar, load_settlement_profile
from .engine import build_settlement_events_from_fills, precheck_orders_against_availability
from .performance import build_account_nav_series
from .report import write_settlement_report
from .reconciliation import reconcile_account_state


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local settlement accounting.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["apply-fills", "settle", "precheck-orders", "reconcile-account", "build-nav", "report", "smoke"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--data-dir", required=True)
        cmd.add_argument("--account-dir", required=True)
        cmd.add_argument("--settlement-dir", required=True)
        cmd.add_argument("--fills-path")
        cmd.add_argument("--broker-store-dir")
        cmd.add_argument("--broker-batch-id")
        cmd.add_argument("--orders-path")
        cmd.add_argument("--corporate-action-dir")
        cmd.add_argument("--corporate-action-ledger-path")
        cmd.add_argument("--prices-path")
        cmd.add_argument("--trade-date")
        cmd.add_argument("--as-of-date", default="20240104")
        cmd.add_argument(
            "--profile",
            choices=["cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"],
            default="cn_ashare_paper_default",
        )
        cmd.add_argument("--cost-basis-method", choices=["average", "fifo"], default="average")
        cmd.add_argument("--allow-unsettled-cash-for-buy", action="store_true")
        cmd.add_argument("--allow-unsettled-shares-for-sell", action="store_true")
        cmd.add_argument("--enforce-available-cash", action="store_true")
        cmd.add_argument("--enforce-available-shares", action="store_true")
        cmd.add_argument("--settle-through-date")
        cmd.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    account = LocalPaperAccount(args.account_dir)
    profile = load_settlement_profile(
        args.profile,
        cost_basis_method=args.cost_basis_method,
        allow_unsettled_cash_for_buy=args.allow_unsettled_cash_for_buy,
        allow_unsettled_shares_for_sell=args.allow_unsettled_shares_for_sell,
    )
    try:
        payload = _run(args, account, profile)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _run(args: argparse.Namespace, account: LocalPaperAccount, profile) -> dict[str, Any]:
    if args.command == "apply-fills":
        fills = read_jsonl(args.fills_path) if args.fills_path else []
        before = len(account.load_state().settlement_events)
        updated = account.apply_fills_settlement_aware(
            fills,
            data_dir=args.data_dir,
            trade_date=args.trade_date or args.as_of_date,
            profile=profile.profile_name,
            prices=_load_prices(args),
            cost_basis_method=profile.cost_basis_method,
        )
        account.save_state(updated)
        paths = write_settlement_report(updated, args.settlement_dir, args.trade_date or args.as_of_date, profile_name=profile.profile_name)
        return {"events": len(updated.settlement_events) - before, "account_cash": updated.cash, "paths": paths}
    if args.command == "settle":
        updated = account.settle(args.settle_through_date or args.as_of_date, prices=_load_prices(args), profile=profile.profile_name)
        paths = write_settlement_report(updated, args.settlement_dir, args.settle_through_date or args.as_of_date, profile_name=profile.profile_name)
        return {"pending_events": sum(event.get("status") == "pending" for event in updated.settlement_events), "cash": updated.cash, "paths": paths}
    if args.command == "precheck-orders":
        orders = read_jsonl(args.orders_path) if args.orders_path else []
        return precheck_orders_against_availability(account.load_state(), orders, prices=_load_prices(args), profile=profile)
    if args.command == "reconcile-account":
        state = account.load_state()
        report = reconcile_account_state(state, as_of_date=args.as_of_date)
        paths = write_settlement_report(state, args.settlement_dir, args.as_of_date, profile_name=profile.profile_name)
        return report.to_dict() | {"paths": paths}
    if args.command == "build-nav":
        state = account.load_state()
        nav = build_account_nav_series(state, prices_by_date={args.as_of_date: _load_prices(args)})
        updated = account.save_state(_replace_account_nav(state, nav))
        paths = write_settlement_report(updated, args.settlement_dir, args.as_of_date, profile_name=profile.profile_name)
        return {"nav_records": [record.to_dict() for record in nav], "paths": paths}
    if args.command == "report":
        return {"paths": write_settlement_report(account.load_state(), args.settlement_dir, args.as_of_date, profile_name=profile.profile_name)}
    if args.command == "smoke":
        return _smoke(args, account, profile)
    raise ValueError(f"unsupported command: {args.command}")


def _smoke(args: argparse.Namespace, account: LocalPaperAccount, profile) -> dict[str, Any]:
    if account.load_state().initial_cash <= 0:
        account.reset(1000000.0)
    daily = read_jsonl(Path(args.data_dir) / "daily_bars" / "records.jsonl")
    if not daily:
        raise ValueError("daily_bars is empty")
    first = sorted(daily, key=lambda row: (row["trade_date"], row["ts_code"]))[0]
    fill = {
        "trade_date": first["trade_date"],
        "ts_code": first["ts_code"],
        "side": "BUY",
        "price": float(first["close"]),
        "shares": 100,
        "value": float(first["close"]) * 100,
        "cost": 5.0,
        "status": "FILLED",
        "broker_fill_id": "settlement_smoke_buy",
    }
    path = Path(args.settlement_dir) / "smoke_fills.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fill, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    args.fills_path = str(path)
    args.trade_date = first["trade_date"]
    return _run(argparse.Namespace(**{**vars(args), "command": "apply-fills"}), account, profile)


def _replace_account_nav(state, nav):
    from dataclasses import replace

    return replace(state, account_nav=[record.to_dict() for record in nav])


def _load_prices(args: argparse.Namespace) -> dict[str, float]:
    if args.prices_path and Path(args.prices_path).exists():
        payload = json.loads(Path(args.prices_path).read_text(encoding="utf-8"))
        return {str(key): float(value) for key, value in payload.items()}
    data_dir = Path(args.data_dir)
    date = args.as_of_date or args.trade_date
    prices: dict[str, float] = {}
    path = data_dir / "daily_bars" / "records.jsonl"
    if path.exists():
        for record in read_jsonl(path):
            if not date or str(record.get("trade_date")) == date:
                prices[str(record.get("ts_code"))] = float(record.get("close") or 0.0)
    return prices


if __name__ == "__main__":
    raise SystemExit(main())
