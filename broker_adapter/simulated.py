"""Local simulated broker adapter."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Sequence

from backtest import AShareCostModel, AShareTradingRules

from .models import (
    BrokerFillRecord,
    BrokerOrderEvent,
    BrokerOrderRecord,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerReconciliationReport,
    BrokerSubmitResult,
)
from .reconciliation import reconcile_broker_batch
from .state_machine import BrokerStateError, can_cancel, can_replace, validate_transition
from .store import LocalBrokerStore, make_order_record


class SimulatedBrokerAdapter:
    def __init__(
        self,
        store_dir: str | Path,
        *,
        prices: dict[str, float] | None = None,
        volumes: dict[str, float] | None = None,
        suspended: dict[str, bool] | None = None,
        limit_up: dict[str, bool] | None = None,
        limit_down: dict[str, bool] | None = None,
        auto_fill: bool = True,
        cost_model: AShareCostModel | None = None,
        trading_rules: AShareTradingRules | None = None,
    ):
        self.store = LocalBrokerStore(store_dir)
        self.prices = prices or {}
        self.volumes = volumes or {}
        self.suspended = suspended or {}
        self.limit_up = limit_up or {}
        self.limit_down = limit_down or {}
        self.auto_fill = bool(auto_fill)
        self.cost_model = cost_model or AShareCostModel()
        self.trading_rules = trading_rules or AShareTradingRules()

    def submit_orders(
        self,
        requests: Sequence[BrokerOrderRequest],
        batch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> BrokerSubmitResult:
        del idempotency_key
        batch = batch_id or (requests[0].batch_id if requests else "")
        orders: list[BrokerOrderRecord] = []
        fills: list[BrokerFillRecord] = []
        events: list[BrokerOrderEvent] = []
        replay_count = 0
        duplicate_count = 0
        for request in requests:
            existing = self.store.get_order_by_client_id(request.client_order_id)
            if existing is not None:
                replay_count += 1
                duplicate_count += 1
                self.store.increment_replay_count(existing.batch_id, existing.client_order_id)
                orders.append(existing)
                fills.extend(self.store.load_fills(broker_order_id=existing.broker_order_id))
                continue
            record = make_order_record(request)
            record = self.store.save_order(record)
            events.append(self._transition(record, BrokerOrderStatus.SUBMITTED, "submitted"))
            record = self.store.get_order(record.broker_order_id) or record
            events.append(self._transition(record, BrokerOrderStatus.ACCEPTED, "accepted"))
            record = self.store.get_order(record.broker_order_id) or record
            if self.auto_fill:
                fill = self._simulate_fill(record)
                fills.append(fill)
                self.store.append_fill(fill)
                next_status = self._status_for_fill(record, fill)
                events.append(self._transition(record, next_status, fill.reason or next_status.lower(), fill=fill))
                record = self.store.get_order(record.broker_order_id) or record
            orders.append(record)
        summary = self.store.write_batch_summary(batch).to_dict() if batch else {}
        summary["idempotent_replay_count"] = replay_count
        summary["duplicate_request_count"] = duplicate_count
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
        record = self._require_order(broker_order_id)
        if not can_cancel(record.status):
            raise BrokerStateError(f"cannot cancel order in status {record.status}")
        self._transition(record, BrokerOrderStatus.CANCEL_PENDING, reason or "cancel_requested")
        record = self._require_order(broker_order_id)
        self._transition(record, BrokerOrderStatus.CANCELLED, reason or "cancelled")
        return self._require_order(broker_order_id)

    def replace_order(
        self,
        broker_order_id: str,
        *,
        shares: int | None = None,
        order_value: float | None = None,
        price: float | None = None,
        reason: str | None = None,
    ) -> BrokerOrderRecord:
        record = self._require_order(broker_order_id)
        if not can_replace(record.status):
            raise BrokerStateError(f"cannot replace order in status {record.status}")
        self._transition(record, BrokerOrderStatus.REPLACE_PENDING, reason or "replace_requested")
        record = self._require_order(broker_order_id)
        request = record.request if isinstance(record.request, BrokerOrderRequest) else BrokerOrderRequest(**record.request)
        replaced_request = replace(
            request,
            shares=int(shares if shares is not None else request.shares),
            order_value=float(order_value if order_value is not None else request.order_value),
            price=float(price if price is not None else request.price),
        )
        replaced = replace(
            record,
            status=BrokerOrderStatus.REPLACED,
            requested_shares=int(max(replaced_request.shares, 0)),
            remaining_shares=max(int(replaced_request.shares) - record.filled_shares, 0),
            requested_value=float(max(replaced_request.order_value, 0.0)),
            replace_count=record.replace_count + 1,
            request=replaced_request,
            updated_at=_utc_now(),
        )
        self.store.save_order(replaced)
        self.store.append_event(self._event(replaced, "replaced", BrokerOrderStatus.REPLACED, reason or "replaced"))
        accepted = self.store.update_order_status(replaced, BrokerOrderStatus.ACCEPTED)
        self.store.append_event(self._event(accepted, "accepted", BrokerOrderStatus.ACCEPTED, "accepted_after_replace"))
        return accepted

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

    def _simulate_fill(self, record: BrokerOrderRecord) -> BrokerFillRecord:
        request = record.request if isinstance(record.request, BrokerOrderRequest) else BrokerOrderRequest(**record.request)
        side = request.side.upper()
        price = float(request.price or self.prices.get(request.ts_code, 0.0) or 0.0)
        reason = ""
        if price <= 0:
            return self._fill(record, price, 0, 0.0, 0.0, "REJECTED", "missing_price")
        if side == "BUY":
            allowed, reason = self.trading_rules.can_buy(
                price,
                is_suspended=bool(self.suspended.get(request.ts_code, False)),
                is_limit_up=bool(self.limit_up.get(request.ts_code, False)),
            )
        else:
            allowed, reason = self.trading_rules.can_sell(
                price,
                is_suspended=bool(self.suspended.get(request.ts_code, False)),
                is_limit_down=bool(self.limit_down.get(request.ts_code, False)),
            )
        if not allowed:
            return self._fill(record, price, 0, 0.0, 0.0, "REJECTED", reason)
        requested_shares = int(max(request.shares, 0))
        if requested_shares <= 0:
            return self._fill(record, price, 0, 0.0, 0.0, "REJECTED", "zero_shares")
        if request.ts_code in self.volumes:
            shares, volume_reason = self.trading_rules.volume_limited_shares(requested_shares, float(self.volumes.get(request.ts_code, 0.0)))
        else:
            shares, volume_reason = requested_shares, ""
        if shares <= 0:
            return self._fill(record, price, 0, 0.0, 0.0, "REJECTED", volume_reason or "zero_shares")
        status = "PARTIAL" if shares < requested_shares else "FILLED"
        value = float(shares * price)
        cost = float(self.cost_model.estimate(side, value).total)
        reason = volume_reason if status == "PARTIAL" else ""
        return self._fill(record, price, int(shares), value, cost, status, reason)

    def _status_for_fill(self, record: BrokerOrderRecord, fill: BrokerFillRecord) -> str:
        if fill.status == "REJECTED":
            return BrokerOrderStatus.REJECTED
        if fill.shares >= record.requested_shares:
            return BrokerOrderStatus.FILLED
        return BrokerOrderStatus.PARTIAL_FILLED

    def _transition(
        self,
        record: BrokerOrderRecord,
        next_status: str,
        message: str,
        *,
        fill: BrokerFillRecord | None = None,
    ) -> BrokerOrderEvent:
        validate_transition(record.status, next_status)
        filled_shares = record.filled_shares
        filled_value = record.filled_value
        avg_fill_price = record.avg_fill_price
        reject_reason = record.reject_reason
        if fill is not None:
            filled_shares = record.filled_shares + max(fill.shares, 0)
            filled_value = record.filled_value + max(fill.value, 0.0)
            avg_fill_price = filled_value / filled_shares if filled_shares > 0 else 0.0
            if fill.status == "REJECTED":
                reject_reason = fill.reason
        updated = self.store.update_order_status(
            record,
            next_status,
            filled_shares=filled_shares,
            filled_value=filled_value,
            avg_fill_price=avg_fill_price,
            reject_reason=reject_reason,
        )
        event = self._event(updated, "status_change", next_status, message)
        self.store.append_event(event)
        return event

    def _event(self, record: BrokerOrderRecord, event_type: str, status: str, message: str) -> BrokerOrderEvent:
        return BrokerOrderEvent(
            event_id=f"be_{record.broker_order_id}_{len(self.store.load_events(batch_id=record.batch_id)) + 1}",
            broker_order_id=record.broker_order_id,
            client_order_id=record.client_order_id,
            batch_id=record.batch_id,
            event_type=event_type,
            status=status,
            created_at=_utc_now(),
            message=message,
        )

    def _fill(
        self,
        record: BrokerOrderRecord,
        price: float,
        shares: int,
        value: float,
        cost: float,
        status: str,
        reason: str,
    ) -> BrokerFillRecord:
        request = record.request if isinstance(record.request, BrokerOrderRequest) else BrokerOrderRequest(**record.request)
        return BrokerFillRecord(
            broker_fill_id=f"bf_{record.broker_order_id}_{max(shares, 0)}_{status.lower()}",
            broker_order_id=record.broker_order_id,
            client_order_id=record.client_order_id,
            batch_id=record.batch_id,
            trade_date=request.trade_date,
            ts_code=request.ts_code,
            side=request.side.upper(),
            price=float(price),
            shares=int(max(shares, 0)),
            value=float(max(value, 0.0)),
            cost=float(max(cost, 0.0)),
            status=status,
            reason=reason,
            parent_order_id=request.parent_order_id,
            child_order_id=request.child_order_id,
            bucket=request.bucket,
            broker_adapter="simulated",
            created_at=_utc_now(),
        )

    def _require_order(self, broker_order_id: str) -> BrokerOrderRecord:
        record = self.store.get_order(broker_order_id)
        if record is None:
            raise KeyError(f"broker order not found: {broker_order_id}")
        return record


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
