"""CLI for broker file dry-run gateway."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .inbox import import_inbox_files, synthesize_inbox_files
from .packager import export_file_batch
from .profiles import get_profile, load_profile
from .report import write_gateway_report
from .roundtrip import run_file_roundtrip_check
from .state import LocalBrokerFileGatewayStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Broker file dry-run gateway.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["init-profile", "validate-profile", "export-outbox", "import-inbox", "synthesize-inbox", "roundtrip-check", "show-batch", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--gateway-store-dir", required=True)
    parser.add_argument("--profile-name", default="generic_broker_csv")
    parser.add_argument("--profile-config")
    parser.add_argument("--output-dir")
    parser.add_argument("--outbox-dir")
    parser.add_argument("--inbox-dir")
    parser.add_argument("--handoff-dir")
    parser.add_argument("--orders-path")
    parser.add_argument("--child-orders-path")
    parser.add_argument("--broker-requests-path")
    parser.add_argument("--broker-store-dir")
    parser.add_argument("--broker-batch-id")
    parser.add_argument("--paper-account-dir")
    parser.add_argument("--settlement-dir")
    parser.add_argument("--approval-id", default="")
    parser.add_argument("--production-run-id", default="")
    parser.add_argument("--trade-date", default="20240104")
    parser.add_argument("--account-id", default="paper_ashare")
    parser.add_argument("--broker-name", default="local_file_dry_run")
    parser.add_argument("--file-batch-id")
    parser.add_argument("--schema-name")
    parser.add_argument("--zip-package", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    profile = load_profile(args.profile_name, args.profile_config)
    output_dir = Path(args.output_dir or args.gateway_store_dir)
    outbox_dir = Path(args.outbox_dir or output_dir / "outbox")
    inbox_dir = Path(args.inbox_dir or output_dir / "inbox")
    if args.command in {"init-profile", "validate-profile"}:
        payload = {"status": "valid", "profile": profile.to_dict(), "profile_notice": profile.notice}
    elif args.command in {"export-outbox", "smoke"}:
        orders = _load_records(args.child_orders_path or args.orders_path or args.broker_requests_path) or _sample_orders(args.trade_date)
        payload = export_file_batch(
            store_dir=args.gateway_store_dir,
            outbox_dir=outbox_dir,
            profile=profile,
            child_orders=orders,
            production_run_id=args.production_run_id or f"prod_{args.trade_date}_file_outbox",
            approval_id=args.approval_id or f"approval_{args.trade_date}_file",
            broker_batch_id=args.broker_batch_id or args.approval_id or f"approval_{args.trade_date}_file",
            trade_date=args.trade_date,
            account_id=args.account_id,
            source_order_paths={"orders_path": str(args.child_orders_path or args.orders_path or "")},
            refresh=args.refresh,
            zip_package=args.zip_package,
            handoff_dir=args.handoff_dir,
        )
        if args.command == "smoke":
            synth = synthesize_inbox_files(outbox_dir=outbox_dir, inbox_dir=inbox_dir, profile=profile, file_batch_id=payload["file_batch_id"])
            imported = import_inbox_files(store_dir=args.gateway_store_dir, inbox_dir=inbox_dir, output_dir=output_dir, profile=profile, file_batch_id=payload["file_batch_id"])
            roundtrip = run_file_roundtrip_check(store_dir=args.gateway_store_dir, outbox_dir=outbox_dir, normalized_dir=output_dir, output_dir=output_dir, file_batch_id=payload["file_batch_id"], broker_batch_id=args.broker_batch_id or args.approval_id or "")
            report = write_gateway_report(store_dir=args.gateway_store_dir, output_dir=output_dir, profile=profile, roundtrip=roundtrip["roundtrip"])
            payload = {"status": "success", "export": payload, "synthesized": synth, "imported": imported, "roundtrip": roundtrip, "report": report}
    elif args.command == "synthesize-inbox":
        payload = {"status": "success", "paths": synthesize_inbox_files(outbox_dir=outbox_dir, inbox_dir=inbox_dir, profile=profile, file_batch_id=args.file_batch_id or "")}
    elif args.command == "import-inbox":
        payload = import_inbox_files(store_dir=args.gateway_store_dir, inbox_dir=inbox_dir, output_dir=output_dir, profile=profile, file_batch_id=args.file_batch_id)
    elif args.command == "roundtrip-check":
        payload = run_file_roundtrip_check(store_dir=args.gateway_store_dir, outbox_dir=outbox_dir, normalized_dir=output_dir, output_dir=output_dir, file_batch_id=args.file_batch_id, broker_batch_id=args.broker_batch_id or "")
    elif args.command == "show-batch":
        store_dir = _resolve_gateway_store_dir(args.gateway_store_dir, args.file_batch_id)
        batch = LocalBrokerFileGatewayStore(store_dir).load_batch(args.file_batch_id)
        payload = {"status": "found" if batch else "missing", "batch": batch.to_dict() if batch else {}}
    elif args.command == "report":
        store_dir = _resolve_gateway_store_dir(args.gateway_store_dir, args.file_batch_id)
        payload = write_gateway_report(store_dir=store_dir, output_dir=output_dir, profile=profile, file_batch_id=args.file_batch_id)
    else:  # pragma: no cover
        payload = {"status": "failed", "error": f"unsupported command: {args.command}"}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 1 if payload.get("status") in {"failed"} else 0


def _load_records(path: str | None) -> list[dict[str, Any]]:
    if not path or not Path(path).exists():
        return []
    target = Path(path)
    if target.suffix == ".json":
        payload = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [dict(item) for item in payload]
        if isinstance(payload, dict):
            for key in ["child_orders", "orders", "requests"]:
                if isinstance(payload.get(key), list):
                    return [dict(item) for item in payload[key]]
            schedule = payload.get("schedule") if isinstance(payload.get("schedule"), dict) else {}
            if isinstance(schedule.get("child_orders"), list):
                return [dict(item) for item in schedule["child_orders"]]
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sample_orders(trade_date: str) -> list[dict[str, Any]]:
    return [
        {"child_order_id": f"child_{trade_date}_buy", "parent_order_id": f"parent_{trade_date}_buy", "trade_date": trade_date, "ts_code": "000001.SZ", "side": "BUY", "bucket": "open", "order_value": 10000.0, "price": 10.0},
        {"child_order_id": f"child_{trade_date}_sell", "parent_order_id": f"parent_{trade_date}_sell", "trade_date": trade_date, "ts_code": "600000.SH", "side": "SELL", "bucket": "close", "order_value": 5000.0, "price": 12.5},
    ]


def _resolve_gateway_store_dir(root_dir: str | Path, file_batch_id: str | None = None) -> Path:
    root = Path(root_dir)
    store = LocalBrokerFileGatewayStore(root)
    if store.load_batch(file_batch_id):
        return root
    candidates = sorted(root.rglob("broker_file_batch_state.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for state_path in candidates:
        candidate = state_path.parent
        if LocalBrokerFileGatewayStore(candidate).load_batch(file_batch_id):
            return candidate
    return root


if __name__ == "__main__":
    raise SystemExit(main())
