"""CLI for operator handoff packages."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from approval import ApprovalBatch, ApprovalOrder, ApprovalStatus, ApprovalType, LocalApprovalStore

from .checklist import required_item_ids
from .evidence import add_evidence_record
from .models import HandoffStatus
from .report import write_operator_handoff_report
from .store import LocalOperatorHandoffStore, create_package


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and manage operator handoff packages.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["create", "mark-item", "add-evidence", "create-approval", "apply-approved", "show", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--handoff-store-dir", required=True)
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--handoff-id")
    parser.add_argument("--file-batch-id", default="file_batch_smoke")
    parser.add_argument("--approval-id", default="approval_smoke")
    parser.add_argument("--production-run-id", default="production_smoke")
    parser.add_argument("--trade-date", default="20240104")
    parser.add_argument("--broker-file-gateway-report-path", default="")
    parser.add_argument("--broker-file-manifest-path", default="")
    parser.add_argument("--checksum-manifest-path", default="")
    parser.add_argument("--outbox-dir", default="")
    parser.add_argument("--handoff-dir", default="")
    parser.add_argument("--mapping-certification-decision-path")
    parser.add_argument("--item-id")
    parser.add_argument("--status", choices=["checked", "failed", "skipped"], default="checked")
    parser.add_argument("--operator")
    parser.add_argument("--checked-by", default="local_operator")
    parser.add_argument("--evidence-path")
    parser.add_argument("--evidence-type", default="review_note")
    parser.add_argument("--description", default="")
    parser.add_argument("--reviewer", default="local_reviewer")
    parser.add_argument("--second-reviewer")
    parser.add_argument("--comment", default="approved_for_file_outbox_dry_run")
    parser.add_argument("--auto-check-all", action="store_true")
    parser.add_argument("--auto-confirm-local-smoke", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = LocalOperatorHandoffStore(args.handoff_store_dir)
    output_dir = Path(args.output_dir or args.handoff_store_dir)
    handoff_id = args.handoff_id or f"handoff_{args.file_batch_id}"
    checked_by = args.operator or args.checked_by
    if args.command in {"show", "report"} and not args.handoff_id:
        resolved = _resolve_handoff(args.handoff_store_dir)
        if resolved is not None:
            args.handoff_store_dir, handoff_id = str(resolved[0]), resolved[1]
            store = LocalOperatorHandoffStore(args.handoff_store_dir)
    if args.command in {"create", "smoke"}:
        existing = store.load_by_file_batch(args.file_batch_id)
        package = existing or create_package(
            handoff_id=handoff_id,
            file_batch_id=args.file_batch_id,
            approval_id=args.approval_id,
            production_run_id=args.production_run_id,
            trade_date=args.trade_date,
            broker_file_gateway_report_path=args.broker_file_gateway_report_path,
            broker_file_manifest_path=args.broker_file_manifest_path,
            checksum_manifest_path=args.checksum_manifest_path,
            outbox_dir=args.outbox_dir,
            handoff_dir=args.handoff_dir or str(output_dir),
            mapping_certification_decision_path=args.mapping_certification_decision_path,
            metadata={"no_real_submit": True, "mode": "file_outbox_dry_run", "second_reviewer": args.second_reviewer},
        )
        store.save_package(package)
        if args.command == "smoke" or args.auto_check_all or args.auto_confirm_local_smoke:
            for item_id in required_item_ids():
                package = store.mark_item(package.handoff_id, item_id, checked=True, status="checked", checked_by=checked_by)
        if args.command == "smoke":
            evidence_path = output_dir / "smoke_evidence.txt"
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text("local smoke evidence for broker file handoff dry-run\n", encoding="utf-8")
            add_evidence_record(
                args.handoff_store_dir,
                package.handoff_id,
                "smoke",
                evidence_path,
                "local smoke evidence",
                recorded_by=checked_by,
                metadata={"local_smoke_auto_confirm": True},
            )
            if args.approval_store_dir:
                _create_local_approval(args.approval_store_dir, package, reviewer=args.reviewer, comment=args.comment)
                package = replace(store.load_package(package.handoff_id), approval_status=ApprovalStatus.approved, local_approval_id=f"handoff_{package.handoff_id}")
                store.save_package(package)
            payload = write_operator_handoff_report(args.handoff_store_dir, package.handoff_id, output_dir)
        else:
            payload = {"status": "success", "handoff_id": package.handoff_id, "handoff_package_path": str(store.package_path(package.handoff_id))}
    elif args.command == "mark-item":
        if not args.item_id:
            raise SystemExit("--item-id is required")
        package = store.mark_item(
            handoff_id,
            args.item_id,
            checked=args.status == "checked",
            status=args.status,
            checked_by=checked_by,
            evidence_path=args.evidence_path,
        )
        payload = {"status": "success", "handoff_id": package.handoff_id, "package_status": package.status}
    elif args.command == "add-evidence":
        if not args.evidence_path:
            raise SystemExit("--evidence-path is required")
        record = add_evidence_record(args.handoff_store_dir, handoff_id, args.evidence_type, args.evidence_path, args.description, recorded_by=checked_by)
        payload = {"status": "success", "evidence": record.to_dict()}
    elif args.command == "create-approval":
        package = store.load_package(handoff_id)
        approval = _create_local_approval(args.approval_store_dir or args.handoff_store_dir, package, reviewer="", comment="")
        payload = {"status": "success", "approval_id": approval.approval_id, "approval_status": approval.status}
    elif args.command == "apply-approved":
        package = store.load_package(handoff_id)
        approval_store = LocalApprovalStore(args.approval_store_dir or args.handoff_store_dir)
        approval_id = package.local_approval_id or f"handoff_{package.handoff_id}"
        approval = approval_store.load_batch(approval_id)
        if approval.status != ApprovalStatus.approved:
            raise SystemExit(f"handoff approval is not approved: {approval_id} is {approval.status}")
        package = replace(package, status=HandoffStatus.approved, approval_status=approval.status, local_approval_id=approval_id)
        store.save_package(package)
        payload = {"status": "success", "handoff_id": package.handoff_id, "approval_id": approval_id}
    elif args.command == "show":
        package = store.load_package(handoff_id)
        payload = {"status": "found", "package": package.to_dict()}
    elif args.command == "report":
        payload = write_operator_handoff_report(args.handoff_store_dir, handoff_id, output_dir)
    else:  # pragma: no cover
        payload = {"status": "failed", "error": f"unsupported command: {args.command}"}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 1 if payload.get("status") == "failed" else 0


def _create_local_approval(store_dir: str | Path, package: Any, reviewer: str = "", comment: str = "") -> ApprovalBatch:
    approval_id = f"handoff_{package.handoff_id}"
    approval = ApprovalBatch(
        approval_id=approval_id,
        created_at=package.created_at,
        factor_id=package.file_batch_id,
        factor_type="broker_file_handoff",
        rebalance_date=package.trade_date,
        portfolio_method="file_outbox_dry_run",
        orders=[ApprovalOrder(trade_date=package.trade_date, ts_code="HANDOFF", side="REVIEW", target_weight=0.0, order_value=0.0, reason="operator_handoff")],
        approval_type=ApprovalType.broker_file_handoff,
        broker_file_batch_id=package.file_batch_id,
        operator_handoff_id=package.handoff_id,
        broker_file_gateway_report_path=package.broker_file_gateway_report_path,
        operator_handoff_report_path="",
        broker_file_summary={"outbox_dir": package.outbox_dir, "no_real_submit": True},
        operator_handoff_summary={"handoff_id": package.handoff_id},
        status=ApprovalStatus.pending,
        metadata={"mode": "file_outbox_dry_run", "no_real_submit": True},
    )
    store = LocalApprovalStore(store_dir)
    store.save_batch(approval)
    if reviewer:
        approval = store.approve(approval.approval_id, reviewer=reviewer, comment=comment)
    return approval


def _resolve_handoff(root_dir: str | Path) -> tuple[Path, str] | None:
    root = Path(root_dir)
    direct = LocalOperatorHandoffStore(root).list_packages()
    if direct:
        return root, sorted(direct, key=lambda package: package.created_at)[-1].handoff_id
    package_paths = sorted(root.rglob("handoffs/*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not package_paths:
        return None
    package_path = package_paths[0]
    return package_path.parent.parent, package_path.stem


if __name__ == "__main__":
    raise SystemExit(main())
