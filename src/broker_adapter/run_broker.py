"""CLI utilities for the local broker adapter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from backtest import AShareTradingRules

from .converters import build_broker_requests_from_child_orders
from .file_adapter import FileInstructionBrokerAdapter
from .models import BrokerAdapterConfig
from .reconciliation import reconcile_broker_batch
from .report import write_broker_report
from .simulated import SimulatedBrokerAdapter
from .store import LocalBrokerStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local broker adapter utilities.")
    parser.add_argument("--store-dir", required=True)
    parser.add_argument("--pretty", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser("submit-simulated")
    submit.add_argument("--child-orders-path", required=True)
    submit.add_argument("--batch-id", required=True)
    submit.add_argument("--trade-date", required=True)
    submit.add_argument("--prices-json", default="")
    submit.add_argument("--auto-fill", action="store_true")
    submit.add_argument("--pretty", action="store_true")

    export = sub.add_parser("export-file")
    export.add_argument("--child-orders-path", required=True)
    export.add_argument("--outbox-dir", required=True)
    export.add_argument("--inbox-dir")
    export.add_argument("--batch-id", required=True)
    export.add_argument("--trade-date", required=True)
    export.add_argument("--schema-name", default="generic_broker_csv")
    export.add_argument("--field-mapping-json", default="")
    export.add_argument("--pretty", action="store_true")

    show = sub.add_parser("show-batch")
    show.add_argument("--batch-id", required=True)
    show.add_argument("--pretty", action="store_true")

    list_orders = sub.add_parser("list-orders")
    list_orders.add_argument("--batch-id")
    list_orders.add_argument("--status")
    list_orders.add_argument("--pretty", action="store_true")

    list_fills = sub.add_parser("list-fills")
    list_fills.add_argument("--batch-id")
    list_fills.add_argument("--broker-order-id")
    list_fills.add_argument("--pretty", action="store_true")

    cancel = sub.add_parser("cancel")
    cancel.add_argument("--broker-order-id", required=True)
    cancel.add_argument("--reason", default="manual_cancel")
    cancel.add_argument("--pretty", action="store_true")

    replace = sub.add_parser("replace")
    replace.add_argument("--broker-order-id", required=True)
    replace.add_argument("--shares", type=int)
    replace.add_argument("--order-value", type=float)
    replace.add_argument("--price", type=float)
    replace.add_argument("--reason", default="manual_replace")
    replace.add_argument("--pretty", action="store_true")

    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--batch-id", required=True)
    reconcile.add_argument("--expected-child-orders-path")
    reconcile.add_argument("--output-dir")
    reconcile.add_argument("--pretty", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "submit-simulated":
            prices = _load_json_arg(args.prices_json)
            child_orders = _read_jsonl(Path(args.child_orders_path))
            requests = build_broker_requests_from_child_orders(child_orders, prices, args.trade_date, args.batch_id, AShareTradingRules())
            adapter = SimulatedBrokerAdapter(args.store_dir, prices=prices, auto_fill=args.auto_fill)
            result = adapter.submit_orders(requests, batch_id=args.batch_id)
            _print(result.to_dict(), args.pretty)
            return 0
        if args.command == "export-file":
            child_orders = _read_jsonl(Path(args.child_orders_path))
            mapping = _load_json_arg(args.field_mapping_json)
            requests = build_broker_requests_from_child_orders(child_orders, {}, args.trade_date, args.batch_id, AShareTradingRules())
            adapter = FileInstructionBrokerAdapter(
                args.store_dir,
                args.outbox_dir,
                args.inbox_dir,
                BrokerAdapterConfig(adapter_type="file", schema_name=args.schema_name, field_mapping=mapping),
            )
            result = adapter.submit_orders(requests, batch_id=args.batch_id)
            _print(result.to_dict(), args.pretty)
            return 0
        if args.command == "show-batch":
            store = LocalBrokerStore(args.store_dir)
            report = reconcile_broker_batch(store, args.batch_id)
            payload = {
                "batch_id": args.batch_id,
                "summary": store.write_batch_summary(args.batch_id).to_dict(),
                "orders": [record.to_dict() for record in store.load_orders(batch_id=args.batch_id)],
                "fills": [record.to_dict() for record in store.load_fills(batch_id=args.batch_id)],
                "reconciliation": report.to_dict(),
            }
            _print(payload, args.pretty)
            return 0
        if args.command == "list-orders":
            store = LocalBrokerStore(args.store_dir)
            _print([record.to_dict() for record in store.load_orders(batch_id=args.batch_id, status=args.status)], args.pretty)
            return 0
        if args.command == "list-fills":
            store = LocalBrokerStore(args.store_dir)
            _print([record.to_dict() for record in store.load_fills(batch_id=args.batch_id, broker_order_id=args.broker_order_id)], args.pretty)
            return 0
        if args.command == "cancel":
            adapter = SimulatedBrokerAdapter(args.store_dir, auto_fill=False)
            _print(adapter.cancel_order(args.broker_order_id, args.reason).to_dict(), args.pretty)
            return 0
        if args.command == "replace":
            adapter = SimulatedBrokerAdapter(args.store_dir, auto_fill=False)
            record = adapter.replace_order(
                args.broker_order_id,
                shares=args.shares,
                order_value=args.order_value,
                price=args.price,
                reason=args.reason,
            )
            _print(record.to_dict(), args.pretty)
            return 0
        if args.command == "reconcile":
            store = LocalBrokerStore(args.store_dir)
            expected = _read_jsonl(Path(args.expected_child_orders_path)) if args.expected_child_orders_path else []
            report = reconcile_broker_batch(store, args.batch_id, expected_child_orders=expected)
            if args.output_dir:
                write_broker_report(store, args.batch_id, report, args.output_dir)
            _print(report.to_dict(), args.pretty)
            return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


def _load_json_arg(value: str) -> dict[str, Any]:
    if not value:
        return {}
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _print(payload: Any, pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
