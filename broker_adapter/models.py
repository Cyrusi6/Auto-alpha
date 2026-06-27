"""Broker adapter dataclasses for local order routing simulations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class BrokerOrderStatus:
    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIAL_FILLED = "PARTIAL_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REPLACE_PENDING = "REPLACE_PENDING"
    REPLACED = "REPLACED"
    EXPORTED = "EXPORTED"
    EXPIRED = "EXPIRED"


TERMINAL_STATUSES = {
    BrokerOrderStatus.FILLED,
    BrokerOrderStatus.REJECTED,
    BrokerOrderStatus.CANCELLED,
    BrokerOrderStatus.EXPIRED,
}


@dataclass(frozen=True)
class BrokerOrderRequest:
    client_order_id: str
    batch_id: str
    trade_date: str
    ts_code: str
    side: str
    shares: int
    order_value: float
    price: float
    price_type: str = "MARKET"
    parent_order_id: str | None = None
    child_order_id: str | None = None
    bucket: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerOrderRecord:
    broker_order_id: str
    client_order_id: str
    batch_id: str
    status: str
    submitted_at: str
    updated_at: str
    requested_shares: int
    filled_shares: int
    remaining_shares: int
    requested_value: float
    filled_value: float
    avg_fill_price: float
    reject_reason: str = ""
    cancel_reason: str = ""
    replace_count: int = 0
    request: BrokerOrderRequest | dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if hasattr(self.request, "to_dict"):
            payload["request"] = self.request.to_dict()
        return payload


@dataclass(frozen=True)
class BrokerOrderEvent:
    event_id: str
    broker_order_id: str
    client_order_id: str
    batch_id: str
    event_type: str
    status: str
    created_at: str
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerFillRecord:
    broker_fill_id: str
    broker_order_id: str
    client_order_id: str
    batch_id: str
    trade_date: str
    ts_code: str
    side: str
    price: float
    shares: int
    value: float
    cost: float
    status: str
    reason: str = ""
    parent_order_id: str | None = None
    child_order_id: str | None = None
    bucket: str | None = None
    broker_adapter: str = "simulated"
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerSubmitResult:
    batch_id: str
    orders: list[BrokerOrderRecord]
    fills: list[BrokerFillRecord] = field(default_factory=list)
    events: list[BrokerOrderEvent] = field(default_factory=list)
    duplicate_request_count: int = 0
    idempotent_replay_count: int = 0
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "orders": [record.to_dict() for record in self.orders],
            "fills": [record.to_dict() for record in self.fills],
            "events": [record.to_dict() for record in self.events],
            "duplicate_request_count": int(self.duplicate_request_count),
            "idempotent_replay_count": int(self.idempotent_replay_count),
            "summary": self.summary,
        }


@dataclass(frozen=True)
class BrokerBatchSummary:
    batch_id: str
    submitted_orders: int
    accepted_orders: int
    filled_orders: int
    partial_orders: int
    rejected_orders: int
    cancelled_orders: int
    open_orders: int
    requested_value: float
    filled_value: float
    unfilled_value: float
    duplicate_request_count: int = 0
    idempotent_replay_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerReconciliationIssue:
    severity: str
    code: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerReconciliationReport:
    batch_id: str
    expected_child_orders: int
    submitted_orders: int
    accepted_orders: int
    filled_orders: int
    partial_orders: int
    rejected_orders: int
    cancelled_orders: int
    open_orders: int
    requested_value: float
    filled_value: float
    unfilled_value: float
    duplicate_request_count: int
    idempotent_replay_count: int
    orphan_fills: int
    missing_fills: int
    status_mismatch_count: int
    account_applied_fills: int
    issues: list[BrokerReconciliationIssue] = field(default_factory=list)
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class BrokerAdapterConfig:
    adapter_type: str = "simulated"
    price_type: str = "MARKET"
    auto_fill: bool = True
    schema_name: str = "generic_broker_csv"
    field_mapping: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
