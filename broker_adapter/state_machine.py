"""Broker order status transition rules."""

from __future__ import annotations

from .models import BrokerOrderStatus, TERMINAL_STATUSES


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    BrokerOrderStatus.NEW: {BrokerOrderStatus.SUBMITTED, BrokerOrderStatus.EXPORTED, BrokerOrderStatus.REJECTED},
    BrokerOrderStatus.SUBMITTED: {BrokerOrderStatus.ACCEPTED, BrokerOrderStatus.REJECTED, BrokerOrderStatus.CANCEL_PENDING},
    BrokerOrderStatus.ACCEPTED: {
        BrokerOrderStatus.PARTIAL_FILLED,
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.REJECTED,
        BrokerOrderStatus.CANCEL_PENDING,
        BrokerOrderStatus.REPLACE_PENDING,
        BrokerOrderStatus.EXPIRED,
    },
    BrokerOrderStatus.PARTIAL_FILLED: {
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.CANCEL_PENDING,
        BrokerOrderStatus.REPLACE_PENDING,
        BrokerOrderStatus.EXPIRED,
    },
    BrokerOrderStatus.CANCEL_PENDING: {BrokerOrderStatus.CANCELLED},
    BrokerOrderStatus.REPLACE_PENDING: {BrokerOrderStatus.REPLACED},
    BrokerOrderStatus.REPLACED: {BrokerOrderStatus.ACCEPTED, BrokerOrderStatus.PARTIAL_FILLED, BrokerOrderStatus.FILLED},
    BrokerOrderStatus.EXPORTED: {BrokerOrderStatus.ACCEPTED, BrokerOrderStatus.PARTIAL_FILLED, BrokerOrderStatus.FILLED, BrokerOrderStatus.REJECTED},
}


class BrokerStateError(ValueError):
    """Raised when a broker order transition is invalid."""


def validate_transition(current_status: str, next_status: str) -> None:
    if current_status in TERMINAL_STATUSES:
        raise BrokerStateError(f"terminal order status cannot transition: {current_status} -> {next_status}")
    allowed = ALLOWED_TRANSITIONS.get(current_status, set())
    if next_status not in allowed:
        raise BrokerStateError(f"invalid broker order transition: {current_status} -> {next_status}")


def can_cancel(status: str) -> bool:
    return status not in TERMINAL_STATUSES and status not in {BrokerOrderStatus.CANCEL_PENDING}


def can_replace(status: str) -> bool:
    return status not in TERMINAL_STATUSES and status not in {BrokerOrderStatus.CANCEL_PENDING, BrokerOrderStatus.REPLACE_PENDING}
