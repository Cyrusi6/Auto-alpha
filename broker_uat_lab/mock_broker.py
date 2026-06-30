"""Deterministic mock BrokerAdapter used for offline UAT."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Sequence

from broker_adapter import (
    BrokerFillRecord,
    BrokerOrderEvent,
    BrokerOrderRecord,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerReconciliationReport,
    BrokerSubmitResult,
    LocalBrokerStore,
)
from broker_adapter.reconciliation import reconcile_broker_batch
from broker_adapter.state_machine import BrokerStateError, can_cancel, can_replace
from broker_adapter.store import make_order_record


class DeterministicMockBrokerAdapter:
    """Small deterministic adapter that never leaves the local process."""

    def __init__(self, store_dir: str | Path, scenario_type: str = "full_fill"):
        self.store = LocalBrokerStore(store_dir)
        self.scenario_type = scenario_type

    def set_scenario(self, scenario_type: str) -> None:
        self.scenario_type = scenario_type

    def submit_orders(
        self,
        requests: Sequence[BrokerOrderRequest],
        batch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> BrokerSubmitResult:
        del idempotency_key
        if self.scenario_type == "rate_limit":
            raise RuntimeError("rate_limit")
        batch = batch_id or (requests[0].batch_id if requests else "uat_batch")
        orders: list[BrokerOrderRecord] = []
        fills: list[BrokerFillRecord] = []
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
                fills.extend(self.store.load_fills(broker_order_id=existing.broker_order_id))
                continue
            record = make_order_record(request)
            record = self.store.save_order(record)
            if self.scenario_type in {"reject_order", "kill_switch_block"}:
                reason = "risk_kill_switch_active" if self.scenario_type == "kill_switch_block" else "mock_reject"
                fill = self._fill(record, 0, "REJECTED", reason)
                self.store.append_fill(fill)
                record = self.store.update_order_status(record, BrokerOrderStatus.REJECTED, reject_reason=reason)
                event = self._event(record, "rejected", reason)
                self.store.append_event(event)
                orders.append(record)
                fills.append(fill)
                events.append(event)
                continue
            if self.scenario_type == "missing_ack":
                orders.append(record)
                continue
            record = self.store.update_order_status(record, BrokerOrderStatus.SUBMITTED)
            event = self._event(record, "submitted", "submitted")
            self.store.append_event(event)
            events.append(event)
            record = self.store.update_order_status(record, BrokerOrderStatus.ACCEPTED)
            event = self._event(record, "accepted", "accepted")
            self.store.append_event(event)
            events.append(event)
            shares = record.requested_shares
            status = "FILLED"
            next_status = BrokerOrderStatus.FILLED
            reason = ""
            if self.scenario_type in {"partial_fill", "out_of_order_fill"}:
                shares = max(record.requested_shares // 2, 1)
                status = "PARTIAL"
                next_status = BrokerOrderStatus.PARTIAL_FILLED
                reason = "mock_partial"
            fill = self._fill(record, shares, status, reason)
            self.store.append_fill(fill)
            if self.scenario_type == "duplicate_fill":
                self.store.append_fill(fill)
            record = self.store.update_order_status(record, next_status, filled_shares=shares, filled_value=fill.value, avg_fill_price=fill.price)
            event = self._event(record, "filled", reason or status.lower())
            if self.scenario_type == "out_of_order_fill":
                events.insert(0, event)
            else:
                events.append(event)
            self.store.append_event(event)
            fills.append(fill)
            orders.append(record)
        summary = self.store.write_batch_summary(batch).to_dict()
        summary.update({"duplicate_request_count": duplicate_count, "idempotent_replay_count": replay_count})
        return BrokerSubmitResult(batch, orders, fills, events, duplicate_count, replay_count, summary)

    def cancel_order(self, broker_order_id: str, reason: str) -> BrokerOrderRecord:
        record = self._require_order(broker_order_id)
        if not can_cancel(record.status):
            raise BrokerStateError(f"cannot cancel order in status {record.status}")
        return self.store.update_order_status(record, BrokerOrderStatus.CANCELLED, cancel_reason=reason)

    def replace_order(
        self,
        broker_order_id: str,
        *,
        shares: int | None = None,
        order_value: float | None = None,
        price: float | None = None,
        reason: str | None = None,
    ) -> BrokerOrderRecord:
        del order_value, price, reason
        record = self._require_order(broker_order_id)
        if not can_replace(record.status):
            raise BrokerStateError(f"cannot replace order in status {record.status}")
        updated = self.store.update_order_status(record, BrokerOrderStatus.REPLACED, replace_count=record.replace_count + 1)
        if shares is not None:
            request = updated.request
            if isinstance(request, dict):
                request = BrokerOrderRequest(**request)
            updated = BrokerOrderRecord(
                **{
                    **updated.to_dict(),
                    "requested_shares": int(shares),
                    "remaining_shares": max(int(shares) - updated.filled_shares, 0),
                    "request": request,
                }
            )
            self.store.save_order(updated)
        return updated

    def get_order(self, broker_order_id: str) -> BrokerOrderRecord | None:
        return self.store.get_order(broker_order_id)

    def list_orders(self, batch_id: str | None = None, status: str | None = None) -> list[BrokerOrderRecord]:
        return self.store.load_orders(batch_id=batch_id, status=status)

    def list_fills(self, batch_id: str | None = None, broker_order_id: str | None = None) -> list[BrokerFillRecord]:
        return self.store.load_fills(batch_id=batch_id, broker_order_id=broker_order_id)

    def reconcile(self, batch_id: str, expected_child_orders=None, account_trades=None) -> BrokerReconciliationReport:
        return reconcile_broker_batch(self.store, batch_id, expected_child_orders, account_trades)

    def _fill(self, record: BrokerOrderRecord, shares: int, status: str, reason: str) -> BrokerFillRecord:
        request = record.request if isinstance(record.request, BrokerOrderRequest) else BrokerOrderRequest(**record.request)
        price = float(request.price or 10.0)
        value = max(int(shares), 0) * price
        return BrokerFillRecord(
            broker_fill_id=f"uat_fill_{record.broker_order_id}_{status.lower()}",
            broker_order_id=record.broker_order_id,
            client_order_id=record.client_order_id,
            batch_id=record.batch_id,
            trade_date=request.trade_date,
            ts_code=request.ts_code,
            side=request.side.upper(),
            price=price,
            shares=max(int(shares), 0),
            value=float(value),
            cost=0.0,
            status=status,
            reason=reason,
            parent_order_id=request.parent_order_id,
            child_order_id=request.child_order_id,
            bucket=request.bucket,
            broker_adapter="mock",
            created_at=_utc_now(),
        )

    def _event(self, record: BrokerOrderRecord, event_type: str, message: str) -> BrokerOrderEvent:
        return BrokerOrderEvent(
            event_id=f"uat_event_{record.broker_order_id}_{event_type}_{len(self.store.load_events(record.batch_id)) + 1}",
            broker_order_id=record.broker_order_id,
            client_order_id=record.client_order_id,
            batch_id=record.batch_id,
            event_type=event_type,
            status=record.status,
            created_at=_utc_now(),
            message=message,
        )

    def _require_order(self, broker_order_id: str) -> BrokerOrderRecord:
        record = self.store.get_order(broker_order_id)
        if record is None:
            raise KeyError(f"broker order not found: {broker_order_id}")
        return record


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
