"""CLI for local approval batches."""

from __future__ import annotations

import argparse
import json

from .store import LocalApprovalStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and decide local order approvals.")
    parser.add_argument("--store-dir", required=True)
    parser.add_argument("--pretty", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list")
    list_parser.add_argument("--status")
    list_parser.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    show_parser = sub.add_parser("show")
    show_parser.add_argument("--approval-id", required=True)
    show_parser.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    approve_parser = sub.add_parser("approve")
    approve_parser.add_argument("--approval-id", required=True)
    approve_parser.add_argument("--reviewer", required=True)
    approve_parser.add_argument("--comment")
    approve_parser.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    reject_parser = sub.add_parser("reject")
    reject_parser.add_argument("--approval-id", required=True)
    reject_parser.add_argument("--reviewer", required=True)
    reject_parser.add_argument("--reason", required=True)
    reject_parser.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    expire_parser = sub.add_parser("expire")
    expire_parser.add_argument("--as-of-time")
    expire_parser.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = LocalApprovalStore(args.store_dir)
    try:
        if args.command == "list":
            payload = {"batches": [_summary(batch.to_dict()) for batch in store.list_batches(status=args.status)]}
        elif args.command == "show":
            payload = store.load_batch(args.approval_id).to_dict()
        elif args.command == "approve":
            payload = store.approve(args.approval_id, args.reviewer, args.comment).to_dict()
        elif args.command == "reject":
            payload = store.reject(args.approval_id, args.reviewer, args.reason).to_dict()
        elif args.command == "expire":
            payload = {"expired": [batch.to_dict() for batch in store.expire_pending(as_of_time=args.as_of_time)]}
        else:  # pragma: no cover
            raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _summary(payload: dict) -> dict:
    return {
        **payload,
        "secret_blocker_count": int((payload.get("compliance_summary") or {}).get("secret_blocker_count", 0) or 0),
        "uat_failed_scenario_count": int((payload.get("broker_uat_summary") or {}).get("failed_count", 0) or 0),
        "compliance_gap_count": int((payload.get("compliance_summary") or {}).get("gap_count", 0) or 0),
        "required_remediation_count": int((payload.get("go_live_summary") or {}).get("required_remediation_count", 0) or 0),
    }


if __name__ == "__main__":
    raise SystemExit(main())
