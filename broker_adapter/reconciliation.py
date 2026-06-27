"""Broker batch reconciliation."""

from __future__ import annotations

from typing import Any, Sequence

from .models import (
    BrokerOrderRecord,
    BrokerOrderStatus,
    BrokerReconciliationIssue,
    BrokerReconciliationReport,
)
from .store import LocalBrokerStore


def reconcile_broker_batch(
    store: LocalBrokerStore,
    batch_id: str,
    expected_child_orders: Sequence[object] | None = None,
    account_trades: Sequence[object] | None = None,
) -> BrokerReconciliationReport:
    orders = store.load_orders(batch_id=batch_id)
    fills = store.load_fills(batch_id=batch_id)
    expected = list(expected_child_orders or [])
    account = [_payload(record) for record in (account_trades or [])]
    issues: list[BrokerReconciliationIssue] = []
    expected_ids = {str(_payload(order).get("child_order_id") or "") for order in expected}
    expected_ids.discard("")
    order_child_ids = {
        str(_request_payload(order).get("child_order_id") or "")
        for order in orders
        if _request_payload(order).get("child_order_id")
    }
    missing_orders = sorted(expected_ids - order_child_ids)
    for child_order_id in missing_orders:
        issues.append(
            BrokerReconciliationIssue(
                severity="error",
                code="missing_order",
                message="expected child order was not submitted to broker store",
                metadata={"child_order_id": child_order_id},
            )
        )
    order_ids = {order.broker_order_id for order in orders}
    orphan_fills = 0
    for fill in fills:
        if fill.broker_order_id not in order_ids:
            orphan_fills += 1
            issues.append(
                BrokerReconciliationIssue(
                    severity="error",
                    code="orphan_fill",
                    message="broker fill has no matching broker order",
                    metadata={"broker_fill_id": fill.broker_fill_id, "broker_order_id": fill.broker_order_id},
                )
            )
    account_fill_ids = {str(record.get("broker_fill_id") or "") for record in account if record.get("broker_fill_id")}
    broker_fill_ids = {fill.broker_fill_id for fill in fills}
    missing_fills = len(broker_fill_ids - account_fill_ids) if account_fill_ids else 0
    if missing_fills:
        issues.append(
            BrokerReconciliationIssue(
                severity="warning",
                code="missing_account_fill",
                message="some broker fills are not present in account trade ledger",
                metadata={"missing_fills": missing_fills},
            )
        )
    status_mismatch_count = _status_mismatch_count(orders)
    if status_mismatch_count:
        issues.append(
            BrokerReconciliationIssue(
                severity="warning",
                code="status_mismatch",
                message="order status does not match filled or remaining share state",
                metadata={"count": status_mismatch_count},
            )
        )
    requested_value = sum(float(order.requested_value) for order in orders)
    filled_value = sum(float(fill.value) for fill in fills if fill.status in {"FILLED", "PARTIAL"})
    return BrokerReconciliationReport(
        batch_id=batch_id,
        expected_child_orders=len(expected),
        submitted_orders=len(orders),
        accepted_orders=sum(1 for order in orders if order.status == BrokerOrderStatus.ACCEPTED),
        filled_orders=sum(1 for order in orders if order.status == BrokerOrderStatus.FILLED),
        partial_orders=sum(1 for order in orders if order.status == BrokerOrderStatus.PARTIAL_FILLED),
        rejected_orders=sum(1 for order in orders if order.status == BrokerOrderStatus.REJECTED),
        cancelled_orders=sum(1 for order in orders if order.status == BrokerOrderStatus.CANCELLED),
        open_orders=sum(1 for order in orders if order.status not in {BrokerOrderStatus.FILLED, BrokerOrderStatus.REJECTED, BrokerOrderStatus.CANCELLED, BrokerOrderStatus.EXPIRED}),
        requested_value=float(requested_value),
        filled_value=float(filled_value),
        unfilled_value=float(max(requested_value - filled_value, 0.0)),
        duplicate_request_count=max(len(orders) - len({order.client_order_id for order in orders}), 0),
        idempotent_replay_count=store.replay_count(batch_id),
        orphan_fills=orphan_fills,
        missing_fills=missing_fills,
        status_mismatch_count=status_mismatch_count,
        account_applied_fills=len(account_fill_ids),
        issues=issues,
    )


def _status_mismatch_count(orders: list[BrokerOrderRecord]) -> int:
    count = 0
    for order in orders:
        if order.status == BrokerOrderStatus.FILLED and order.remaining_shares != 0:
            count += 1
        if order.status == BrokerOrderStatus.PARTIAL_FILLED and order.remaining_shares <= 0:
            count += 1
    return count


def _request_payload(order: BrokerOrderRecord) -> dict[str, Any]:
    request = order.request
    if hasattr(request, "to_dict"):
        return request.to_dict()
    return dict(request)


def _payload(record: object) -> dict[str, Any]:
    if hasattr(record, "to_dict"):
        return record.to_dict()
    if hasattr(record, "__dataclass_fields__"):
        return {field: getattr(record, field) for field in record.__dataclass_fields__}
    return dict(record)
