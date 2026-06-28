"""Local JSON/JSONL store for broker adapter state."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import (
    BrokerBatchSummary,
    BrokerFillRecord,
    BrokerOrderEvent,
    BrokerOrderRecord,
    BrokerOrderRequest,
    BrokerOrderStatus,
)


class LocalBrokerStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.orders_jsonl_path = self.root_dir / "broker_orders.jsonl"
        self.order_state_path = self.root_dir / "broker_order_state.json"
        self.events_path = self.root_dir / "broker_events.jsonl"
        self.fills_path = self.root_dir / "broker_fills.jsonl"
        self.batches_path = self.root_dir / "broker_batches.json"

    def load_orders(self, batch_id: str | None = None, status: str | None = None) -> list[BrokerOrderRecord]:
        state = self._load_state()
        records = [_order_from_payload(payload) for payload in state.get("orders", {}).values()]
        if batch_id is not None:
            records = [record for record in records if record.batch_id == batch_id]
        if status is not None:
            records = [record for record in records if record.status == status]
        return sorted(records, key=lambda record: record.broker_order_id)

    def get_order(self, broker_order_id: str) -> BrokerOrderRecord | None:
        payload = self._load_state().get("orders", {}).get(broker_order_id)
        return _order_from_payload(payload) if payload else None

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderRecord | None:
        state = self._load_state()
        broker_order_id = state.get("client_index", {}).get(client_order_id)
        if broker_order_id is None:
            return None
        payload = state.get("orders", {}).get(broker_order_id)
        return _order_from_payload(payload) if payload else None

    def save_order(self, record: BrokerOrderRecord) -> BrokerOrderRecord:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        state = self._load_state()
        state.setdefault("orders", {})[record.broker_order_id] = record.to_dict()
        state.setdefault("client_index", {})[record.client_order_id] = record.broker_order_id
        self.order_state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        self._rewrite_orders_jsonl()
        self.write_batch_summary(record.batch_id)
        return record

    def append_event(self, event: BrokerOrderEvent) -> BrokerOrderEvent:
        _append_jsonl(self.events_path, event.to_dict())
        return event

    def append_fill(self, fill: BrokerFillRecord) -> BrokerFillRecord:
        existing_ids = {record.broker_fill_id for record in self.load_fills()}
        if fill.broker_fill_id not in existing_ids:
            _append_jsonl(self.fills_path, fill.to_dict())
        return fill

    def load_events(self, batch_id: str | None = None) -> list[BrokerOrderEvent]:
        events = [_event_from_payload(payload) for payload in _read_jsonl(self.events_path)]
        if batch_id is not None:
            events = [event for event in events if event.batch_id == batch_id]
        return events

    def load_fills(self, batch_id: str | None = None, broker_order_id: str | None = None) -> list[BrokerFillRecord]:
        fills = [_fill_from_payload(payload) for payload in _read_jsonl(self.fills_path)]
        if batch_id is not None:
            fills = [fill for fill in fills if fill.batch_id == batch_id]
        if broker_order_id is not None:
            fills = [fill for fill in fills if fill.broker_order_id == broker_order_id]
        return fills

    def update_order_status(
        self,
        record: BrokerOrderRecord,
        status: str,
        *,
        filled_shares: int | None = None,
        filled_value: float | None = None,
        avg_fill_price: float | None = None,
        reject_reason: str = "",
        cancel_reason: str = "",
        replace_count: int | None = None,
    ) -> BrokerOrderRecord:
        filled = int(filled_shares if filled_shares is not None else record.filled_shares)
        requested = int(record.requested_shares)
        updated = replace(
            record,
            status=status,
            updated_at=_utc_now(),
            filled_shares=filled,
            remaining_shares=max(requested - filled, 0),
            filled_value=float(filled_value if filled_value is not None else record.filled_value),
            avg_fill_price=float(avg_fill_price if avg_fill_price is not None else record.avg_fill_price),
            reject_reason=reject_reason or record.reject_reason,
            cancel_reason=cancel_reason or record.cancel_reason,
            replace_count=int(replace_count if replace_count is not None else record.replace_count),
        )
        return self.save_order(updated)

    def write_batch_summary(self, batch_id: str) -> BrokerBatchSummary:
        summary = summarize_orders(batch_id, self.load_orders(batch_id=batch_id), self.load_fills(batch_id=batch_id))
        self.root_dir.mkdir(parents=True, exist_ok=True)
        payload = _read_json(self.batches_path)
        payload[batch_id] = summary.to_dict()
        self.batches_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return summary

    def load_batch_summary(self, batch_id: str) -> BrokerBatchSummary | None:
        payload = _read_json(self.batches_path).get(batch_id)
        return BrokerBatchSummary(**payload) if payload else None

    def increment_replay_count(self, batch_id: str, client_order_id: str) -> None:
        state = self._load_state()
        key = f"{batch_id}:{client_order_id}"
        replays = state.setdefault("replays", {})
        replays[key] = int(replays.get(key, 0)) + 1
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.order_state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def replay_count(self, batch_id: str) -> int:
        state = self._load_state()
        prefix = f"{batch_id}:"
        return sum(int(value) for key, value in state.get("replays", {}).items() if str(key).startswith(prefix))

    def _load_state(self) -> dict[str, Any]:
        if not self.order_state_path.exists():
            return {"orders": {}, "client_index": {}, "replays": {}}
        try:
            return json.loads(self.order_state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"orders": {}, "client_index": {}, "replays": {}}

    def _rewrite_orders_jsonl(self) -> None:
        records = [record.to_dict() for record in self.load_orders()]
        self.orders_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.orders_jsonl_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                handle.write("\n")


def summarize_orders(batch_id: str, orders: list[BrokerOrderRecord], fills: list[BrokerFillRecord]) -> BrokerBatchSummary:
    status_counts: dict[str, int] = {}
    for order in orders:
        status_counts[order.status] = status_counts.get(order.status, 0) + 1
    requested = sum(float(order.requested_value) for order in orders)
    filled = sum(float(fill.value) for fill in fills if fill.status in {"FILLED", "PARTIAL"})
    open_orders = sum(1 for order in orders if order.status not in {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"})
    return BrokerBatchSummary(
        batch_id=batch_id,
        submitted_orders=len(orders),
        accepted_orders=status_counts.get(BrokerOrderStatus.ACCEPTED, 0),
        filled_orders=status_counts.get(BrokerOrderStatus.FILLED, 0),
        partial_orders=status_counts.get(BrokerOrderStatus.PARTIAL_FILLED, 0),
        rejected_orders=status_counts.get(BrokerOrderStatus.REJECTED, 0),
        cancelled_orders=status_counts.get(BrokerOrderStatus.CANCELLED, 0),
        open_orders=open_orders,
        requested_value=float(requested),
        filled_value=float(filled),
        unfilled_value=float(max(requested - filled, 0.0)),
    )


def make_order_record(request: BrokerOrderRequest, broker_order_id: str | None = None, status: str = BrokerOrderStatus.NEW) -> BrokerOrderRecord:
    now = _utc_now()
    order_id = broker_order_id or f"bo_{_safe_id(request.batch_id)}_{_safe_id(request.client_order_id)}"
    return BrokerOrderRecord(
        broker_order_id=order_id,
        client_order_id=request.client_order_id,
        batch_id=request.batch_id,
        status=status,
        submitted_at=now,
        updated_at=now,
        requested_shares=int(max(request.shares, 0)),
        filled_shares=0,
        remaining_shares=int(max(request.shares, 0)),
        requested_value=float(max(request.order_value, 0.0)),
        filled_value=0.0,
        avg_fill_price=0.0,
        request=request,
    )


def _order_from_payload(payload: dict[str, Any]) -> BrokerOrderRecord:
    request_payload = payload.get("request") or {}
    request = BrokerOrderRequest(**request_payload) if isinstance(request_payload, dict) else request_payload
    return BrokerOrderRecord(
        broker_order_id=str(payload.get("broker_order_id") or ""),
        client_order_id=str(payload.get("client_order_id") or ""),
        batch_id=str(payload.get("batch_id") or ""),
        status=str(payload.get("status") or BrokerOrderStatus.NEW),
        submitted_at=str(payload.get("submitted_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
        requested_shares=int(payload.get("requested_shares") or 0),
        filled_shares=int(payload.get("filled_shares") or 0),
        remaining_shares=int(payload.get("remaining_shares") or 0),
        requested_value=float(payload.get("requested_value") or 0.0),
        filled_value=float(payload.get("filled_value") or 0.0),
        avg_fill_price=float(payload.get("avg_fill_price") or 0.0),
        reject_reason=str(payload.get("reject_reason") or ""),
        cancel_reason=str(payload.get("cancel_reason") or ""),
        replace_count=int(payload.get("replace_count") or 0),
        request=request,
    )


def _event_from_payload(payload: dict[str, Any]) -> BrokerOrderEvent:
    return BrokerOrderEvent(
        event_id=str(payload.get("event_id") or ""),
        broker_order_id=str(payload.get("broker_order_id") or ""),
        client_order_id=str(payload.get("client_order_id") or ""),
        batch_id=str(payload.get("batch_id") or ""),
        event_type=str(payload.get("event_type") or ""),
        status=str(payload.get("status") or ""),
        created_at=str(payload.get("created_at") or ""),
        message=str(payload.get("message") or ""),
        metadata=dict(payload.get("metadata") or {}),
    )


def _fill_from_payload(payload: dict[str, Any]) -> BrokerFillRecord:
    return BrokerFillRecord(
        broker_fill_id=str(payload.get("broker_fill_id") or ""),
        broker_order_id=str(payload.get("broker_order_id") or ""),
        client_order_id=str(payload.get("client_order_id") or ""),
        batch_id=str(payload.get("batch_id") or ""),
        trade_date=str(payload.get("trade_date") or ""),
        ts_code=str(payload.get("ts_code") or ""),
        side=str(payload.get("side") or ""),
        price=float(payload.get("price") or 0.0),
        shares=int(payload.get("shares") or 0),
        value=float(payload.get("value") or 0.0),
        cost=float(payload.get("cost") or 0.0),
        status=str(payload.get("status") or ""),
        reason=str(payload.get("reason") or ""),
        commission=float(payload.get("commission") or 0.0),
        stamp_duty=float(payload.get("stamp_duty") or 0.0),
        transfer_fee=float(payload.get("transfer_fee") or 0.0),
        slippage=float(payload.get("slippage") or 0.0),
        market_impact=float(payload.get("market_impact") or 0.0),
        other_fee=float(payload.get("other_fee") or 0.0),
        cost_breakdown=dict(payload.get("cost_breakdown") or {}),
        parent_order_id=payload.get("parent_order_id"),
        child_order_id=payload.get("child_order_id"),
        bucket=payload.get("bucket"),
        broker_adapter=str(payload.get("broker_adapter") or "simulated"),
        created_at=payload.get("created_at"),
        metadata=dict(payload.get("metadata") or {}),
    )


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in str(value)).strip("_")
