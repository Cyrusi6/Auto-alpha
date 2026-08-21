"""CLI for local corporate action reports and accounting."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from auto_alpha.platform.artifacts.storage import publish_generation

from .normalizer import normalize_corporate_action_records
from .report import read_jsonl, write_corporate_action_report
from .reconciliation import (
    derive_causal_adjustment_factor_vintages,
    reconcile_adjustment_factors_with_actions,
)
from .schedule import build_action_schedule
from .total_return import build_total_return_series


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize, report, and apply local A-share corporate actions.")
    sub = parser.add_subparsers(dest="command", required=True)
    vintage = sub.add_parser("derive-adjustment-vintage")
    vintage.add_argument("--daily-bars", required=True)
    vintage.add_argument("--event-versions", required=True)
    vintage.add_argument("--output-dir", required=True)
    vintage.add_argument("--pretty", action="store_true")
    for name in ("normalize", "validate", "build-schedule", "build-total-return", "reconcile-adjustment", "report", "apply-account"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--data-dir", required=True)
        cmd.add_argument("--account-dir")
        cmd.add_argument("--output-dir", required=True)
        cmd.add_argument("--start-date", default="00000000")
        cmd.add_argument("--end-date", default="99999999")
        cmd.add_argument("--as-of-date")
        cmd.add_argument("--trade-date")
        cmd.add_argument("--cash-field", choices=("cash_div", "cash_div_tax"), default="cash_div")
        cmd.add_argument("--apply-statuses", default="实施")
        cmd.add_argument("--application-date-mode", choices=("ex_date", "pay_date", "div_listdate", "record_date"), default="pay_date")
        cmd.add_argument("--total-return-mode", choices=("price_only", "cash_dividend", "cash_reinvested"), default="cash_reinvested")
        cmd.add_argument("--reconcile-adjustment", action="store_true")
        cmd.add_argument("--settlement-aware", action="store_true")
        cmd.add_argument("--settlement-dir")
        cmd.add_argument(
            "--settlement-profile",
            choices=("cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"),
            default="cn_ashare_paper_default",
        )
        cmd.add_argument("--cost-basis-method", choices=("average", "fifo"), default="average")
        cmd.add_argument("--tolerance", type=float, default=0.05)
        cmd.add_argument("--strict", action="store_true")
        cmd.add_argument("--fail-on-error", action="store_true")
        cmd.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    indent = 2 if args.pretty else None
    if args.command == "derive-adjustment-vintage":
        try:
            bars = read_jsonl(args.daily_bars)
            events = read_jsonl(args.event_versions)
            derived = derive_causal_adjustment_factor_vintages(
                bars,
                events,
            )
            rows_payload = b"".join(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                + b"\n"
                for row in derived["rows"]
            )
            semantic = {
                key: value
                for key, value in derived.items()
                if key not in {"rows", "content_hash"}
            } | {
                "derivation_content_hash": derived["content_hash"],
                "rows_file_sha256": hashlib.sha256(
                    rows_payload
                ).hexdigest(),
                "safety": {
                    "data_admission_eligible": False,
                    "alpha_search_authorized": False,
                    "holdout_activation_authorized": False,
                    "shadow_trading_authorized": False,
                    "paper_trading_authorized": False,
                    "live_trading_authorized": False,
                },
            }
            published = publish_generation(
                args.output_dir,
                prefix="causal_adjustment_vintage",
                manifest_name="causal_adjustment_vintage_manifest.json",
                semantic=semantic,
                extra_files={"causal_adjustment_factors.jsonl": rows_payload},
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False, indent=indent))
            return 2
        print(json.dumps(published, ensure_ascii=False, indent=indent, sort_keys=True))
        return 0 if derived["derivation_complete"] is True else 2
    try:
        events = _load_events(args.data_dir, args.output_dir, args.cash_field, args.apply_statuses)
        output = Path(args.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        if args.command in {"normalize", "validate", "report"}:
            paths = write_corporate_action_report(
                args.data_dir,
                events,
                output,
                start_date=args.start_date,
                end_date=args.end_date,
                total_return_mode=args.total_return_mode,
                reconcile_adjustment=args.reconcile_adjustment or args.command == "report",
                tolerance=args.tolerance,
            )
            payload = {"events": len(events), "paths": paths}
            if args.command == "validate":
                validation = json.loads(Path(paths["corporate_action_validation_report_path"]).read_text(encoding="utf-8"))
                payload.update(validation)
        elif args.command == "build-schedule":
            schedule = build_action_schedule(events, args.start_date, args.end_date, include_proposals=True)
            path = output / "corporate_action_schedule.json"
            path.write_text(json.dumps({"schedule": schedule, "records": len(schedule)}, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            payload = {"records": len(schedule), "corporate_action_schedule_path": str(path)}
        elif args.command == "build-total-return":
            records = build_total_return_series(args.data_dir, events, mode=args.total_return_mode)
            from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact

            series_path = write_jsonl_artifact(output / "total_return_series.jsonl", [record.to_dict() for record in records], "total_return_series", "corporate_actions")
            report_path = write_json_artifact(
                output / "total_return_report.json",
                {
                    "total_return_mode": args.total_return_mode,
                    "records": len(records),
                    "cash_dividend_amount": sum(record.cash_dividend for record in records),
                    "stock_distribution_ratio_sum": sum(record.stock_distribution_ratio for record in records),
                },
                "total_return_report",
                "corporate_actions",
            )
            (output / "total_return_report.md").write_text(f"# Total Return Report\n\n- records: {len(records)}\n", encoding="utf-8")
            payload = {"records": len(records), "total_return_series_path": str(series_path), "total_return_report_path": str(report_path)}
        elif args.command == "reconcile-adjustment":
            payload = reconcile_adjustment_factors_with_actions(args.data_dir, events, tolerance=args.tolerance)
            path = output / "adjustment_factor_reconciliation.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            payload["adjustment_reconciliation_path"] = str(path)
        elif args.command == "apply-account":
            if not args.account_dir or not args.trade_date:
                raise ValueError("apply-account requires --account-dir and --trade-date")
            from auto_alpha.execution.trading.paper import LocalPaperAccount

            account = LocalPaperAccount(args.account_dir)
            state, applications = account.apply_corporate_actions(
                events,
                trade_date=args.trade_date,
                mode=args.application_date_mode,
            )
            settlement_paths = {}
            if args.settlement_aware and args.settlement_dir:
                from auto_alpha.execution.settlement.engine import SettlementCalendar, apply_settlement_events, build_settlement_events_from_corporate_actions, load_settlement_profile
                from auto_alpha.execution.settlement.engine import write_settlement_report

                profile = load_settlement_profile(args.settlement_profile, cost_basis_method=args.cost_basis_method)
                calendar = SettlementCalendar.from_data_dir(args.data_dir)
                settlement_events = build_settlement_events_from_corporate_actions(applications, profile=profile, calendar=calendar, account_id=state.account_id)
                state = account.save_state(apply_settlement_events(state, settlement_events, args.trade_date, profile=profile))
                settlement_paths = write_settlement_report(state, args.settlement_dir, args.trade_date, profile_name=profile.profile_name)
            payload = {
                "account_dir": args.account_dir,
                "applications": [application.to_dict() for application in applications],
                "applied_corporate_action_count": sum(application.status == "APPLIED" for application in applications),
                "cash": state.cash,
                "positions": {key: value.to_dict() for key, value in state.positions.items()},
                "corporate_action_ledger_path": str(account.corporate_action_ledger_path),
                "settlement_ledger_path": str(account.settlement_ledger_path),
                "settlement_paths": settlement_paths,
            }
        else:  # pragma: no cover
            raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=indent))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=True))
    if args.fail_on_error and int(payload.get("corporate_action_error_count") or payload.get("error_count") or 0) > 0:
        return 3
    return 0


def _load_events(data_dir: str, output_dir: str, cash_field: str, apply_statuses: str):
    events_path = Path(output_dir) / "corporate_action_events.jsonl"
    if events_path.exists():
        from .models import CorporateActionEvent

        return [CorporateActionEvent(**row) for row in read_jsonl(events_path)]
    records = read_jsonl(Path(data_dir) / "corporate_actions" / "records.jsonl")
    statuses = tuple(item.strip() for item in apply_statuses.split(",") if item.strip())
    return normalize_corporate_action_records(records, apply_statuses=statuses or ("实施",), cash_field=cash_field)


if __name__ == "__main__":
    raise SystemExit(main())
