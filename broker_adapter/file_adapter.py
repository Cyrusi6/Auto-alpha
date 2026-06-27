"""File instruction broker adapter skeleton."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .models import (
    BrokerAdapterConfig,
    BrokerFillRecord,
    BrokerOrderEvent,
    BrokerOrderRecord,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerReconciliationReport,
    BrokerSubmitResult,
)
from .reconciliation import reconcile_broker_batch
from .store import LocalBrokerStore, make_order_record


INTERNAL_FIELDS = [
    "client_order_id",
    "trade_date",
    "ts_code",
    "side",
    "shares",
    "price",
    "price_type",
    "order_value",
    "parent_order_id",
    "child_order_id",
    "bucket",
]


class FileInstructionBrokerAdapter:
    def __init__(
        self,
        store_dir: str | Path,
        outbox_dir: str | Path,
        inbox_dir: str | Path | None = None,
        config: BrokerAdapterConfig | None = None,
    ):
        self.store = LocalBrokerStore(store_dir)
        self.outbox_dir = Path(outbox_dir)
        self.inbox_dir = Path(inbox_dir) if inbox_dir is not None else None
        self.config = config or BrokerAdapterConfig(adapter_type="file", schema_name="generic_broker_csv")

    def submit_orders(
        self,
        requests: Sequence[BrokerOrderRequest],
        batch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> BrokerSubmitResult:
        del idempotency_key
        batch = batch_id or (requests[0].batch_id if requests else "")
        orders: list[BrokerOrderRecord] = []
        events: list[BrokerOrderEvent] = []
        duplicate_count = 0
        replay_count = 0
        for request in requests:
            existing = self.store.get_order_by_client_id(request.client_order_id)
            if existing is not None:
                duplicate_count += 1
                replay_count += 1
                self.store.increment_replay_count(existing.batch_id, existing.client_order_id)
                orders.append(existing)
                continue
            record = make_order_record(request, status=BrokerOrderStatus.EXPORTED)
            self.store.save_order(record)
            event = BrokerOrderEvent(
                event_id=f"be_{record.broker_order_id}_exported",
                broker_order_id=record.broker_order_id,
                client_order_id=record.client_order_id,
                batch_id=record.batch_id,
                event_type="exported",
                status=BrokerOrderStatus.EXPORTED,
                created_at=_utc_now(),
                message="file_instruction_exported",
                metadata={"schema_name": self.config.schema_name},
            )
            self.store.append_event(event)
            orders.append(record)
            events.append(event)
        manifest_path = self._write_outbox(batch, requests)
        fills = self._import_inbox_fills(batch)
        summary = self.store.write_batch_summary(batch).to_dict() if batch else {}
        summary.update(
            {
                "outbox_manifest_path": str(manifest_path),
                "schema_name": self.config.schema_name,
                "inbox_fills": len(fills),
                "idempotent_replay_count": replay_count,
                "duplicate_request_count": duplicate_count,
            }
        )
        return BrokerSubmitResult(
            batch_id=batch,
            orders=orders,
            fills=fills,
            events=events,
            duplicate_request_count=duplicate_count,
            idempotent_replay_count=replay_count,
            summary=summary,
        )

    def cancel_order(self, broker_order_id: str, reason: str) -> BrokerOrderRecord:
        record = self.store.get_order(broker_order_id)
        if record is None:
            raise KeyError(f"broker order not found: {broker_order_id}")
        updated = self.store.update_order_status(record, BrokerOrderStatus.CANCELLED, cancel_reason=reason)
        self.store.append_event(
            BrokerOrderEvent(
                event_id=f"be_{broker_order_id}_cancelled",
                broker_order_id=broker_order_id,
                client_order_id=record.client_order_id,
                batch_id=record.batch_id,
                event_type="cancelled",
                status=BrokerOrderStatus.CANCELLED,
                created_at=_utc_now(),
                message=reason,
            )
        )
        return updated

    def replace_order(
        self,
        broker_order_id: str,
        *,
        shares: int | None = None,
        order_value: float | None = None,
        price: float | None = None,
        reason: str | None = None,
    ) -> BrokerOrderRecord:
        del shares, order_value, price
        record = self.store.get_order(broker_order_id)
        if record is None:
            raise KeyError(f"broker order not found: {broker_order_id}")
        return self.store.update_order_status(record, BrokerOrderStatus.REPLACED, replace_count=record.replace_count + 1)

    def get_order(self, broker_order_id: str) -> BrokerOrderRecord | None:
        return self.store.get_order(broker_order_id)

    def list_orders(self, batch_id: str | None = None, status: str | None = None) -> list[BrokerOrderRecord]:
        return self.store.load_orders(batch_id=batch_id, status=status)

    def list_fills(self, batch_id: str | None = None, broker_order_id: str | None = None) -> list[BrokerFillRecord]:
        return self.store.load_fills(batch_id=batch_id, broker_order_id=broker_order_id)

    def reconcile(
        self,
        batch_id: str,
        expected_child_orders=None,
        account_trades=None,
    ) -> BrokerReconciliationReport:
        return reconcile_broker_batch(self.store, batch_id, expected_child_orders, account_trades)

    def _write_outbox(self, batch_id: str, requests: Sequence[BrokerOrderRequest]) -> Path:
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        rows = [request.to_dict() for request in requests]
        jsonl_path = self.outbox_dir / "broker_orders.jsonl"
        csv_path = self.outbox_dir / "broker_orders.csv"
        manifest_path = self.outbox_dir / "broker_instruction_manifest.json"
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(_mapped(row, self.config.field_mapping), ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[self.config.field_mapping.get(field, field) for field in INTERNAL_FIELDS])
            writer.writeheader()
            for row in rows:
                writer.writerow(_mapped({field: row.get(field) for field in INTERNAL_FIELDS}, self.config.field_mapping))
        manifest = {
            "batch_id": batch_id,
            "schema_name": self.config.schema_name,
            "created_at": _utc_now(),
            "orders": len(rows),
            "csv_path": str(csv_path),
            "jsonl_path": str(jsonl_path),
            "field_mapping": self.config.field_mapping,
            "notice": "generic file instruction skeleton; validate field mapping manually before any external use",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        summary_path = self.outbox_dir / "broker_batch_summary.json"
        summary_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return manifest_path

    def _import_inbox_fills(self, batch_id: str) -> list[BrokerFillRecord]:
        if self.inbox_dir is None or not self.inbox_dir.exists():
            return []
        records = _read_records(self.inbox_dir / "broker_fills.jsonl")
        if not records and (self.inbox_dir / "broker_fills.csv").exists():
            with (self.inbox_dir / "broker_fills.csv").open("r", encoding="utf-8", newline="") as handle:
                records = list(csv.DictReader(handle))
        fills: list[BrokerFillRecord] = []
        reverse_mapping = {value: key for key, value in self.config.field_mapping.items()}
        for payload in records:
            row = {reverse_mapping.get(key, key): value for key, value in payload.items()}
            broker_order_id = str(row.get("broker_order_id") or "")
            order = self.store.get_order(broker_order_id) if broker_order_id else self.store.get_order_by_client_id(str(row.get("client_order_id") or ""))
            if order is None:
                continue
            request = order.request if isinstance(order.request, BrokerOrderRequest) else BrokerOrderRequest(**order.request)
            fill = BrokerFillRecord(
                broker_fill_id=str(row.get("broker_fill_id") or f"bf_{order.broker_order_id}_inbox"),
                broker_order_id=order.broker_order_id,
                client_order_id=order.client_order_id,
                batch_id=batch_id,
                trade_date=str(row.get("trade_date") or request.trade_date),
                ts_code=str(row.get("ts_code") or request.ts_code),
                side=str(row.get("side") or request.side),
                price=float(row.get("price") or request.price or 0.0),
                shares=int(float(row.get("shares") or 0)),
                value=float(row.get("value") or 0.0),
                cost=float(row.get("cost") or 0.0),
                status=str(row.get("status") or "FILLED"),
                reason=str(row.get("reason") or ""),
                parent_order_id=request.parent_order_id,
                child_order_id=request.child_order_id,
                bucket=request.bucket,
                broker_adapter="file",
                created_at=_utc_now(),
            )
            self.store.append_fill(fill)
            if fill.status == "REJECTED":
                self.store.update_order_status(order, BrokerOrderStatus.REJECTED, reject_reason=fill.reason)
            elif fill.shares >= order.requested_shares:
                self.store.update_order_status(
                    order,
                    BrokerOrderStatus.FILLED,
                    filled_shares=fill.shares,
                    filled_value=fill.value,
                    avg_fill_price=fill.price,
                )
            elif fill.shares > 0:
                self.store.update_order_status(
                    order,
                    BrokerOrderStatus.PARTIAL_FILLED,
                    filled_shares=fill.shares,
                    filled_value=fill.value,
                    avg_fill_price=fill.price,
                )
            fills.append(fill)
        return fills


def _mapped(row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    return {mapping.get(key, key): value for key, value in row.items() if key in INTERNAL_FIELDS or key not in {"metadata"}}


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
