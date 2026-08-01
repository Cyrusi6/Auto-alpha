"""Broker adapter protocol."""

from __future__ import annotations

from typing import Protocol, Sequence

from .models import BrokerFillRecord, BrokerOrderRecord, BrokerOrderRequest, BrokerReconciliationReport, BrokerSubmitResult


class BrokerAdapter(Protocol):
    def submit_orders(
        self,
        requests: Sequence[BrokerOrderRequest],
        batch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> BrokerSubmitResult:
        ...

    def cancel_order(self, broker_order_id: str, reason: str) -> BrokerOrderRecord:
        ...

    def replace_order(
        self,
        broker_order_id: str,
        *,
        shares: int | None = None,
        order_value: float | None = None,
        price: float | None = None,
        reason: str | None = None,
    ) -> BrokerOrderRecord:
        ...

    def get_order(self, broker_order_id: str) -> BrokerOrderRecord | None:
        ...

    def list_orders(self, batch_id: str | None = None, status: str | None = None) -> list[BrokerOrderRecord]:
        ...

    def list_fills(self, batch_id: str | None = None, broker_order_id: str | None = None) -> list[BrokerFillRecord]:
        ...

    def reconcile(
        self,
        batch_id: str,
        expected_child_orders=None,
        account_trades=None,
    ) -> BrokerReconciliationReport:
        ...
