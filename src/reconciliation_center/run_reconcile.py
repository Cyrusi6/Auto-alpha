"""CLI for EOD broker statement reconciliation and adjustment workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from approval import LocalApprovalStore

from .adjustments import apply_approved_adjustments, create_adjustment_approval, create_adjustment_proposals, save_adjustment_proposals
from .eod import run_eod_reconciliation
from .models import ReconciliationBreak, ReconciliationMaterialityConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local EOD reconciliation against broker statement artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    for command_name in ("eod", "propose-adjustments", "create-approval", "apply-approved", "show-breaks", "show-summary"):
        command = sub.add_parser(command_name)
        command.add_argument("--statement-dir")
        command.add_argument("--broker-store-dir")
        command.add_argument("--broker-batch-id")
        command.add_argument("--paper-account-dir")
        command.add_argument("--settlement-dir")
        command.add_argument("--corporate-action-dir")
        command.add_argument("--approval-store-dir")
        command.add_argument("--output-dir")
        command.add_argument("--account-id", default="paper_ashare")
        command.add_argument("--trade-date", default="")
        command.add_argument("--as-of-date", default="")
        command.add_argument("--materiality-config")
        command.add_argument("--strict", action="store_true")
        command.add_argument("--fail-on-break", action="store_true")
        command.add_argument("--fail-on-error", action="store_true")
        command.add_argument("--create-adjustment-proposals", action="store_true")
        command.add_argument("--create-adjustment-approval", action="store_true")
        command.add_argument("--approval-id")
        command.add_argument("--reviewer", default="")
        command.add_argument("--comment")
        command.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "eod":
            report, _mirror, paths = _run_eod(args, create_proposals=args.create_adjustment_proposals)
            payload = report.to_dict() | {"paths": {key: str(value) for key, value in paths.items()}}
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
            return _exit_code(report, args)
        if args.command == "propose-adjustments":
            report, _mirror, paths = _run_eod(args, create_proposals=True)
            payload = {
                "status": report.status,
                "adjustment_proposals_path": str(paths.get("adjustment_proposals_path", "")),
                "adjustment_proposal_batch_path": str(paths.get("adjustment_proposal_batch_path", "")),
                "proposal_count": int(report.summary.get("adjustment_proposal_count", 0) or 0),
                "paths": {key: str(value) for key, value in paths.items()},
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
            return _exit_code(report, args)
        if args.command == "create-approval":
            report, _mirror, paths = _run_eod(args, create_proposals=True)
            batch_path = paths.get("adjustment_proposal_batch_path")
            if not batch_path or not Path(batch_path).exists():
                batch = create_adjustment_proposals(
                    report.breaks,
                    _materiality(args),
                    account_id=args.account_id,
                    trade_date=args.trade_date or report.trade_date,
                    as_of_date=args.as_of_date or report.as_of_date,
                )
                proposal_paths = save_adjustment_proposals(batch, args.output_dir or ".")
                paths.update(proposal_paths)
            else:
                batch_payload = json.loads(Path(batch_path).read_text(encoding="utf-8"))
                batch = create_adjustment_proposals(
                    [ReconciliationBreak(**payload) for payload in report.to_dict().get("breaks", [])],
                    _materiality(args),
                    account_id=str(batch_payload.get("account_id") or args.account_id),
                    trade_date=str(batch_payload.get("trade_date") or args.trade_date or report.trade_date),
                    as_of_date=str(batch_payload.get("as_of_date") or args.as_of_date or report.as_of_date),
                )
            if not args.approval_store_dir:
                raise ValueError("--approval-store-dir is required for create-approval")
            approval = create_adjustment_approval(
                batch,
                args.approval_store_dir,
                reconciliation_report_path=str(paths.get("eod_reconciliation_report_path", "")),
                adjustment_proposals_path=str(paths.get("adjustment_proposals_path", "")),
                metadata={
                    "eod_reconciliation_status": report.status,
                    "unresolved_break_count": report.summary.get("unresolved_break_count", 0),
                    "material_break_count": report.summary.get("material_break_count", 0),
                },
            )
            payload = {
                "approval_id": approval.approval_id,
                "approval_status": approval.status,
                "approval_type": approval.approval_type,
                "proposal_count": approval.adjustment_summary.get("proposal_count", 0),
                "adjustment_summary": approval.adjustment_summary,
                "paths": {key: str(value) for key, value in paths.items()},
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
            return _exit_code(report, args)
        if args.command == "apply-approved":
            if not args.approval_store_dir or not args.approval_id or not args.paper_account_dir:
                raise ValueError("--approval-store-dir, --approval-id and --paper-account-dir are required for apply-approved")
            result, paths = apply_approved_adjustments(
                args.approval_store_dir,
                args.approval_id,
                args.paper_account_dir,
                args.output_dir or ".",
                account_id=args.account_id,
                trade_date=args.trade_date,
            )
            payload = result.to_dict() | {"paths": {key: str(value) for key, value in paths.items()}}
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
            return 0
        if args.command == "show-breaks":
            path = Path(args.statement_dir or args.output_dir or ".") / "reconciliation_breaks.jsonl"
            payload = {"breaks": _read_jsonl(path), "break_count": len(_read_jsonl(path))}
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
            return 0
        if args.command == "show-summary":
            path = Path(args.statement_dir or args.output_dir or ".") / "eod_reconciliation_report.json"
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            print(json.dumps(payload.get("summary", payload), ensure_ascii=False, indent=2 if args.pretty else None))
            return 0
    except Exception as exc:  # noqa: BLE001 - CLI should return structured errors
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None))
        return 1
    return 2


def _run_eod(args, *, create_proposals: bool):
    if not args.statement_dir or not args.output_dir:
        raise ValueError("--statement-dir and --output-dir are required")
    return run_eod_reconciliation(
        statement_dir=args.statement_dir,
        output_dir=args.output_dir,
        broker_store_dir=args.broker_store_dir,
        broker_batch_id=args.broker_batch_id,
        paper_account_dir=args.paper_account_dir,
        settlement_dir=args.settlement_dir,
        corporate_action_dir=args.corporate_action_dir,
        account_id=args.account_id,
        trade_date=args.trade_date,
        as_of_date=args.as_of_date,
        materiality=_materiality(args),
        strict=args.strict,
        create_adjustment_proposals=create_proposals,
    )


def _materiality(args) -> ReconciliationMaterialityConfig:
    if not args.materiality_config:
        return ReconciliationMaterialityConfig()
    payload = json.loads(Path(args.materiality_config).read_text(encoding="utf-8"))
    return ReconciliationMaterialityConfig(**payload)


def _exit_code(report, args) -> int:
    summary = report.summary
    if args.fail_on_break and int(summary.get("break_count", 0) or 0):
        return 1
    if args.fail_on_error and (int(summary.get("error_count", 0) or 0) or int(summary.get("blocker_count", 0) or 0)):
        return 1
    return 0


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
