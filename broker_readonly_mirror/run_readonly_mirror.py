"""CLI for broker read-only mirror snapshots."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from approval import ApprovalStatus, ApprovalType, LocalApprovalStore
from broker_connectivity.network_guard import build_network_guard
from broker_connectivity.profiles import build_broker_connection_profile, load_broker_connection_profile
from broker_connectivity.readonly_client import build_readonly_client

from .mirror_store import LocalBrokerReadonlyMirrorStore
from .models import BrokerReadonlySnapshot, BrokerReadonlySnapshotStatus
from .normalizer import normalize_readonly_payload
from .reconciliation import reconcile_readonly_mirror
from .report import write_readonly_mirror_artifacts


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and reconcile read-only broker mirror artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["snapshot", "normalize", "reconcile", "export-statement", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_common_args(cmd)
    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--connectivity-report-path")
    parser.add_argument("--profile-name", default="mock_readonly")
    parser.add_argument("--profile-config")
    parser.add_argument("--connectivity-store-dir")
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--approval-id")
    parser.add_argument("--mirror-store-dir")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--trade-date", default="20240104")
    parser.add_argument("--as-of-date", default="20240104")
    parser.add_argument("--account-id", default="paper_account")
    parser.add_argument("--broker-name")
    parser.add_argument("--paper-account-dir")
    parser.add_argument("--broker-store-dir")
    parser.add_argument("--settlement-dir")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--require-approval", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        _validate_approval_gate(args)
        snapshot = _build_snapshot(args)
        store = LocalBrokerReadonlyMirrorStore(args.mirror_store_dir or args.output_dir)
        snapshot = store.save_snapshot(snapshot, refresh=args.refresh)
        reconciliation_report = reconcile_readonly_mirror(
            snapshot,
            paper_account_dir=args.paper_account_dir,
            broker_store_dir=args.broker_store_dir,
            settlement_dir=args.settlement_dir,
        )
        paths = write_readonly_mirror_artifacts(
            output_dir=args.output_dir,
            snapshot=snapshot,
            reconciliation_report=reconciliation_report,
        )
        payload = {
            "status": snapshot.status,
            "snapshot_id": snapshot.snapshot_id,
            "connectivity_session_id": snapshot.connectivity_session_id,
            "broker_name": snapshot.broker_name,
            "account_id": snapshot.account_id,
            "readonly_position_count": len(snapshot.positions),
            "readonly_order_count": len(snapshot.orders),
            "readonly_fill_count": len(snapshot.fills),
            "readonly_mirror_break_count": int(reconciliation_report.get("break_count", 0) or 0),
            "real_submit_supported": False,
            "paths": paths,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI returns structured errors
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None))
        return 1


def _validate_approval_gate(args: argparse.Namespace) -> None:
    if not args.require_approval:
        return
    if not args.approval_store_dir or not args.approval_id:
        raise ValueError("--approval-store-dir and --approval-id are required when --require-approval is set")
    batch = LocalApprovalStore(args.approval_store_dir).load_batch(args.approval_id)
    if batch.status != ApprovalStatus.approved:
        raise ValueError(f"broker connectivity review is not approved: {args.approval_id} is {batch.status}")
    if batch.approval_type != ApprovalType.broker_connectivity_review:
        raise ValueError(f"approval is not a broker_connectivity_review: {args.approval_id}")


def _build_snapshot(args: argparse.Namespace) -> BrokerReadonlySnapshot:
    source = _load_payload_from_connectivity_report(args.connectivity_report_path)
    profile_payload = source.get("profile") if isinstance(source.get("profile"), dict) else {}
    session_payload = source.get("session") if isinstance(source.get("session"), dict) else {}
    probe_payload = source.get("probe_result") if isinstance(source.get("probe_result"), dict) else {}

    account_id = args.account_id or str(probe_payload.get("account_id") or session_payload.get("account_id") or "paper_account")
    broker_name = args.broker_name or str(profile_payload.get("broker_name") or probe_payload.get("broker_name") or "mock_broker")
    trade_date = args.trade_date or str(probe_payload.get("trade_date") or session_payload.get("trade_date") or "")
    as_of_date = args.as_of_date or str(probe_payload.get("as_of_date") or session_payload.get("as_of_date") or trade_date)
    connectivity_session_id = str(session_payload.get("session_id") or "readonly_fixture_session")

    if source:
        metadata = probe_payload.get("metadata") if isinstance(probe_payload.get("metadata"), dict) else {}
        payload = {
            "account_snapshot": probe_payload.get("account_snapshot") if isinstance(probe_payload.get("account_snapshot"), dict) else {},
            "positions": metadata.get("positions") if isinstance(metadata.get("positions"), list) else [],
            "orders": metadata.get("orders") if isinstance(metadata.get("orders"), list) else [],
            "fills": metadata.get("fills") if isinstance(metadata.get("fills"), list) else [],
            "statements": metadata.get("statements") if isinstance(metadata.get("statements"), list) else [],
        }
    else:
        payload = _load_payload_from_profile(args, account_id, broker_name, trade_date, as_of_date)
        connectivity_session_id = "readonly_direct_mock_session"

    normalized = normalize_readonly_payload(
        payload,
        account_id=account_id,
        broker_name=broker_name,
        trade_date=trade_date,
        as_of_date=as_of_date,
    )
    issues = [issue.to_dict() for issue in normalized.get("issues", [])]
    status = BrokerReadonlySnapshotStatus.warning if issues else BrokerReadonlySnapshotStatus.success
    created = _utc_now()
    return BrokerReadonlySnapshot(
        snapshot_id=f"readonly_snapshot_{_utc_id(created)}",
        connectivity_session_id=connectivity_session_id,
        account_id=account_id,
        broker_name=broker_name,
        trade_date=trade_date,
        as_of_date=as_of_date,
        status=status,
        cash=normalized["cash"].to_dict() if normalized.get("cash") else {},
        positions=[item.to_dict() for item in normalized.get("positions", [])],
        orders=[item.to_dict() for item in normalized.get("orders", [])],
        fills=[item.to_dict() for item in normalized.get("fills", [])],
        statements=[item.to_dict() for item in normalized.get("statements", [])],
        source_hash=str(normalized.get("source_hash") or ""),
        created_at=created,
        issues=issues,
        metadata={"real_submit_supported": False, "source": "connectivity_report" if source else "direct_readonly_client"},
    )


def _load_payload_from_connectivity_report(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    payload = json.loads(target.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _load_payload_from_profile(args: argparse.Namespace, account_id: str, broker_name: str, trade_date: str, as_of_date: str) -> dict[str, Any]:
    if args.profile_config:
        profile = load_broker_connection_profile(args.profile_config)
    else:
        profile = build_broker_connection_profile(args.profile_name, broker_name=broker_name, account_id=account_id)
    guard = build_network_guard(profile, allow_network=args.allow_network)
    client = build_readonly_client(profile, guard, account_id, trade_date, as_of_date)
    return {
        "account_snapshot": client.get_account_snapshot(),
        "positions": client.list_positions(trade_date, as_of_date),
        "orders": client.list_orders(trade_date, as_of_date),
        "fills": client.list_fills(trade_date, as_of_date),
        "statements": client.list_statements(trade_date, as_of_date),
    }


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _utc_id(value: str) -> str:
    return value.replace("-", "").replace(":", "").replace("Z", "")


if __name__ == "__main__":
    raise SystemExit(main())
