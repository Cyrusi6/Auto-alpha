"""Broker adapter models, state, storage, reconciliation, and execution."""

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
    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0
    slippage: float = 0.0
    market_impact: float = 0.0
    other_fee: float = 0.0
    cost_breakdown: dict[str, float] = field(default_factory=dict)
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

from typing import Protocol, Sequence



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

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any



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
        events = [_event_from_payload(payload) for payload in _adapter_store_read_jsonl(self.events_path)]
        if batch_id is not None:
            events = [event for event in events if event.batch_id == batch_id]
        return events

    def load_fills(self, batch_id: str | None = None, broker_order_id: str | None = None) -> list[BrokerFillRecord]:
        fills = [_fill_from_payload(payload) for payload in _adapter_store_read_jsonl(self.fills_path)]
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
            updated_at=_adapter_store_utc_now(),
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
    now = _adapter_store_utc_now()
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


def _adapter_store_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _adapter_store_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in str(value)).strip("_")

from typing import Any, Sequence



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

import hashlib
from typing import Any, Sequence

from auto_alpha.portfolio.simulator.backtest import AShareTradingRules
from auto_alpha.execution.trading.engine import ExecutionFill
from auto_alpha.execution.trading.plan import ChildOrder



def build_broker_requests_from_child_orders(
    child_orders: Sequence[ChildOrder | dict[str, Any]],
    prices: dict[str, float],
    trade_date: str,
    batch_id: str,
    trading_rules: AShareTradingRules | None = None,
    price_type: str = "MARKET",
) -> list[BrokerOrderRequest]:
    trading_rules = trading_rules or AShareTradingRules()
    requests: list[BrokerOrderRequest] = []
    for raw in child_orders:
        order = _child_payload(raw)
        ts_code = str(order.get("ts_code") or "")
        price = float(prices.get(ts_code, 0.0) or 0.0)
        order_value = float(order.get("order_value") or 0.0)
        shares = trading_rules.round_shares(order_value / price) if price > 0 else 0
        child_order_id = order.get("child_order_id")
        client_order_id = str(child_order_id or _client_order_id(batch_id, order))
        requests.append(
            BrokerOrderRequest(
                client_order_id=client_order_id,
                batch_id=batch_id,
                trade_date=trade_date,
                ts_code=ts_code,
                side=str(order.get("side") or "").upper(),
                shares=int(shares),
                order_value=order_value,
                price=price,
                price_type=price_type,
                parent_order_id=order.get("parent_order_id"),
                child_order_id=child_order_id,
                bucket=order.get("bucket"),
                metadata={"source": "child_order", "reason": order.get("reason", "")},
            )
        )
    return requests


def broker_fills_to_execution_fills(fills: Sequence[BrokerFillRecord | dict[str, Any]]) -> list[ExecutionFill]:
    records: list[ExecutionFill] = []
    for fill in fills:
        payload = fill.to_dict() if hasattr(fill, "to_dict") else dict(fill)
        records.append(
            ExecutionFill(
                trade_date=str(payload.get("trade_date") or ""),
                ts_code=str(payload.get("ts_code") or ""),
                side=str(payload.get("side") or "").upper(),
                price=float(payload.get("price") or 0.0),
                shares=int(payload.get("shares") or 0),
                value=float(payload.get("value") or 0.0),
                status=str(payload.get("status") or ""),
                cost=float(payload.get("cost") or 0.0),
                reason=str(payload.get("reason") or ""),
                parent_order_id=payload.get("parent_order_id"),
                child_order_id=payload.get("child_order_id"),
                bucket=payload.get("bucket"),
                broker_order_id=payload.get("broker_order_id"),
                broker_fill_id=payload.get("broker_fill_id"),
                client_order_id=payload.get("client_order_id"),
                broker_adapter=payload.get("broker_adapter"),
                broker_batch_id=payload.get("batch_id"),
                commission=float(payload.get("commission") or 0.0),
                stamp_duty=float(payload.get("stamp_duty") or 0.0),
                transfer_fee=float(payload.get("transfer_fee") or 0.0),
                slippage=float(payload.get("slippage") or 0.0),
                market_impact=float(payload.get("market_impact") or 0.0),
                other_fee=float(payload.get("other_fee") or 0.0),
                cost_breakdown=dict(payload.get("cost_breakdown") or {}),
            )
        )
    return records


def execution_fills_to_broker_fills(
    fills: Sequence[ExecutionFill | dict[str, Any]],
    batch_id: str,
    adapter_name: str = "paper",
) -> list[BrokerFillRecord]:
    records: list[BrokerFillRecord] = []
    for fill in fills:
        payload = fill.__dict__ if hasattr(fill, "__dataclass_fields__") else dict(fill)
        broker_order_id = str(payload.get("broker_order_id") or payload.get("child_order_id") or _fill_hash(payload, batch_id))
        broker_fill_id = str(payload.get("broker_fill_id") or f"bf_{_fill_hash(payload, batch_id)}")
        records.append(
            BrokerFillRecord(
                broker_fill_id=broker_fill_id,
                broker_order_id=broker_order_id,
                client_order_id=str(payload.get("client_order_id") or payload.get("child_order_id") or broker_order_id),
                batch_id=batch_id,
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
                broker_adapter=adapter_name,
            )
        )
    return records


def _child_payload(order: ChildOrder | dict[str, Any]) -> dict[str, Any]:
    if hasattr(order, "to_dict"):
        return order.to_dict()
    return dict(order)


def _client_order_id(batch_id: str, payload: dict[str, Any]) -> str:
    base = "|".join(
        [
            batch_id,
            str(payload.get("ts_code") or ""),
            str(payload.get("side") or ""),
            str(payload.get("bucket") or ""),
            str(payload.get("order_value") or ""),
        ]
    )
    return "co_" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:20]


def _fill_hash(payload: dict[str, Any], batch_id: str) -> str:
    base = "|".join(
        [
            batch_id,
            str(payload.get("trade_date") or ""),
            str(payload.get("child_order_id") or ""),
            str(payload.get("ts_code") or ""),
            str(payload.get("side") or ""),
            str(payload.get("shares") or ""),
            str(payload.get("value") or ""),
            str(payload.get("status") or ""),
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]

from dataclasses import asdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Sequence

from auto_alpha.portfolio.simulator.backtest import AShareCostModel, AShareTradingRules
from auto_alpha.portfolio.risk.controls import LocalRiskControlState



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
        risk_control_state_dir: str | Path | None = None,
        risk_policy_path: str | Path | None = None,
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
        self.risk_control_state_dir = Path(risk_control_state_dir) if risk_control_state_dir is not None else None
        self.risk_policy_path = Path(risk_policy_path) if risk_policy_path is not None else None

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
            if self._risk_kill_switch_active():
                fill = self._fill(record, float(request.price or 0.0), 0, 0.0, 0.0, "REJECTED", "risk_kill_switch_active")
                fills.append(fill)
                self.store.append_fill(fill)
                events.append(self._transition(record, BrokerOrderStatus.REJECTED, "risk_kill_switch_active", fill=fill))
                record = self.store.get_order(record.broker_order_id) or record
                orders.append(record)
                continue
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

    def _risk_kill_switch_active(self) -> bool:
        if self.risk_control_state_dir is None:
            return False
        return LocalRiskControlState(self.risk_control_state_dir).load_kill_switch().active

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
            updated_at=_adapter_simulated_utc_now(),
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
        breakdown = self.cost_model.estimate(side, value)
        cost = float(breakdown.total)
        reason = volume_reason if status == "PARTIAL" else ""
        return self._fill(record, price, int(shares), value, cost, status, reason, asdict(breakdown))

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
            created_at=_adapter_simulated_utc_now(),
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
        cost_breakdown: dict[str, float] | None = None,
    ) -> BrokerFillRecord:
        request = record.request if isinstance(record.request, BrokerOrderRequest) else BrokerOrderRequest(**record.request)
        cost_breakdown = cost_breakdown or {}
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
            commission=float(cost_breakdown.get("commission", 0.0) or 0.0),
            stamp_duty=float(cost_breakdown.get("stamp_duty", 0.0) or 0.0),
            transfer_fee=float(cost_breakdown.get("transfer_fee", 0.0) or 0.0),
            slippage=float(cost_breakdown.get("slippage", 0.0) or 0.0),
            market_impact=float(cost_breakdown.get("market_impact", 0.0) or 0.0),
            other_fee=float(cost_breakdown.get("other_fee", 0.0) or 0.0),
            cost_breakdown=cost_breakdown,
            parent_order_id=request.parent_order_id,
            child_order_id=request.child_order_id,
            bucket=request.bucket,
            broker_adapter="simulated",
            created_at=_adapter_simulated_utc_now(),
        )

    def _require_order(self, broker_order_id: str) -> BrokerOrderRecord:
        record = self.store.get_order(broker_order_id)
        if record is None:
            raise KeyError(f"broker order not found: {broker_order_id}")
        return record


def _adapter_simulated_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import csv
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact
from auto_alpha.portfolio.risk.controls import LocalRiskControlState



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
        risk_control_state_dir: str | Path | None = None,
        risk_policy_path: str | Path | None = None,
    ):
        self.store = LocalBrokerStore(store_dir)
        self.outbox_dir = Path(outbox_dir)
        self.inbox_dir = Path(inbox_dir) if inbox_dir is not None else None
        self.config = config or BrokerAdapterConfig(adapter_type="file", schema_name="generic_broker_csv")
        self.risk_control_state_dir = Path(risk_control_state_dir) if risk_control_state_dir is not None else None
        self.risk_policy_path = Path(risk_policy_path) if risk_policy_path is not None else None

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
        export_requests: list[BrokerOrderRequest] = []
        for request in requests:
            existing = self.store.get_order_by_client_id(request.client_order_id)
            if existing is not None:
                duplicate_count += 1
                replay_count += 1
                self.store.increment_replay_count(existing.batch_id, existing.client_order_id)
                orders.append(existing)
                continue
            if self._risk_kill_switch_active():
                record = make_order_record(request, status=BrokerOrderStatus.REJECTED)
                record = replace(record, reject_reason="risk_kill_switch_active")
                record = self.store.save_order(record)
                event = BrokerOrderEvent(
                    event_id=f"be_{record.broker_order_id}_risk_rejected",
                    broker_order_id=record.broker_order_id,
                    client_order_id=record.client_order_id,
                    batch_id=record.batch_id,
                    event_type="rejected",
                    status=BrokerOrderStatus.REJECTED,
                    created_at=_adapter_file_adapter_utc_now(),
                    message="risk_kill_switch_active",
                )
                self.store.append_event(event)
                orders.append(record)
                events.append(event)
                continue
            record = make_order_record(request, status=BrokerOrderStatus.EXPORTED)
            self.store.save_order(record)
            export_requests.append(request)
            event = BrokerOrderEvent(
                event_id=f"be_{record.broker_order_id}_exported",
                broker_order_id=record.broker_order_id,
                client_order_id=record.client_order_id,
                batch_id=record.batch_id,
                event_type="exported",
                status=BrokerOrderStatus.EXPORTED,
                created_at=_adapter_file_adapter_utc_now(),
                message="file_instruction_exported",
                metadata={"schema_name": self.config.schema_name},
            )
            self.store.append_event(event)
            orders.append(record)
            events.append(event)
        manifest_path = self._write_outbox(batch, export_requests)
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

    def _risk_kill_switch_active(self) -> bool:
        if self.risk_control_state_dir is None:
            return False
        return LocalRiskControlState(self.risk_control_state_dir).load_kill_switch().active

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
                created_at=_adapter_file_adapter_utc_now(),
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
        write_jsonl_artifact(
            jsonl_path,
            [_mapped(row, self.config.field_mapping) for row in rows],
            artifact_type="broker_orders",
            producer="broker_adapter",
            extra={"schema_name": self.config.schema_name},
        )
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[self.config.field_mapping.get(field, field) for field in INTERNAL_FIELDS])
            writer.writeheader()
            for row in rows:
                writer.writerow(_mapped({field: row.get(field) for field in INTERNAL_FIELDS}, self.config.field_mapping))
        manifest = {
            "batch_id": batch_id,
            "schema_name": self.config.schema_name,
            "created_at": _adapter_file_adapter_utc_now(),
            "orders": len(rows),
            "csv_path": str(csv_path),
            "jsonl_path": str(jsonl_path),
            "field_mapping": self.config.field_mapping,
            "notice": "generic file instruction skeleton; validate field mapping manually before any external use",
        }
        write_json_artifact(manifest_path, manifest, artifact_type="broker_instruction_manifest", producer="broker_adapter")
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
                commission=float(row.get("commission") or 0.0),
                stamp_duty=float(row.get("stamp_duty") or 0.0),
                transfer_fee=float(row.get("transfer_fee") or 0.0),
                slippage=float(row.get("slippage") or 0.0),
                market_impact=float(row.get("market_impact") or 0.0),
                other_fee=float(row.get("other_fee") or 0.0),
                cost_breakdown={
                    "commission": float(row.get("commission") or 0.0),
                    "stamp_duty": float(row.get("stamp_duty") or 0.0),
                    "transfer_fee": float(row.get("transfer_fee") or 0.0),
                    "slippage": float(row.get("slippage") or 0.0),
                    "market_impact": float(row.get("market_impact") or 0.0),
                    "other_fee": float(row.get("other_fee") or 0.0),
                    "total": float(row.get("cost") or 0.0),
                },
                parent_order_id=request.parent_order_id,
                child_order_id=request.child_order_id,
                bucket=request.bucket,
                broker_adapter="file",
                created_at=_adapter_file_adapter_utc_now(),
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


def _adapter_file_adapter_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import json
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def write_broker_report(
    store: LocalBrokerStore,
    batch_id: str,
    reconciliation: BrokerReconciliationReport | None,
    output_dir: str | Path,
) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    orders = [record.to_dict() for record in store.load_orders(batch_id=batch_id)]
    events = [record.to_dict() for record in store.load_events(batch_id=batch_id)]
    fills = [record.to_dict() for record in store.load_fills(batch_id=batch_id)]
    summary = store.write_batch_summary(batch_id).to_dict()
    report = {
        "batch_id": batch_id,
        "summary": summary,
        "orders": orders,
        "fills": fills,
        "events": events,
        "reconciliation": reconciliation.to_dict() if reconciliation else {},
    }
    paths = {
        "broker_report_path": root / "broker_report.json",
        "broker_report_md_path": root / "broker_report.md",
        "broker_orders_path": root / "broker_orders.jsonl",
        "broker_events_path": root / "broker_events.jsonl",
        "broker_fills_path": root / "broker_fills.jsonl",
        "broker_reconciliation_path": root / "broker_reconciliation.json",
        "broker_reconciliation_md_path": root / "broker_reconciliation.md",
    }
    write_json_artifact(paths["broker_report_path"], report, artifact_type="broker_report", producer="broker_adapter")
    write_jsonl_artifact(paths["broker_orders_path"], orders, artifact_type="broker_orders", producer="broker_adapter")
    write_jsonl_artifact(paths["broker_events_path"], events, artifact_type="broker_events", producer="broker_adapter")
    write_jsonl_artifact(paths["broker_fills_path"], fills, artifact_type="broker_fills", producer="broker_adapter")
    recon_payload = reconciliation.to_dict() if reconciliation else {}
    write_json_artifact(paths["broker_reconciliation_path"], recon_payload, artifact_type="broker_reconciliation", producer="broker_adapter")
    paths["broker_report_md_path"].write_text(_markdown_report(report), encoding="utf-8")
    paths["broker_reconciliation_md_path"].write_text(_markdown_reconciliation(recon_payload), encoding="utf-8")
    return paths


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Broker Report",
        "",
        f"- batch_id: `{report.get('batch_id')}`",
        f"- submitted_orders: `{summary.get('submitted_orders', 0)}`",
        f"- filled_orders: `{summary.get('filled_orders', 0)}`",
        f"- partial_orders: `{summary.get('partial_orders', 0)}`",
        f"- rejected_orders: `{summary.get('rejected_orders', 0)}`",
        f"- open_orders: `{summary.get('open_orders', 0)}`",
        f"- unfilled_value: `{summary.get('unfilled_value', 0.0)}`",
    ]
    return "\n".join(lines) + "\n"


def _markdown_reconciliation(payload: dict[str, Any]) -> str:
    lines = [
        "# Broker Reconciliation",
        "",
        f"- batch_id: `{payload.get('batch_id', '')}`",
        f"- expected_child_orders: `{payload.get('expected_child_orders', 0)}`",
        f"- submitted_orders: `{payload.get('submitted_orders', 0)}`",
        f"- orphan_fills: `{payload.get('orphan_fills', 0)}`",
        f"- missing_fills: `{payload.get('missing_fills', 0)}`",
        f"- status_mismatch_count: `{payload.get('status_mismatch_count', 0)}`",
        "",
        "| severity | code | message |",
        "| --- | --- | --- |",
    ]
    for issue in payload.get("issues", []) if isinstance(payload.get("issues"), list) else []:
        if not isinstance(issue, dict):
            continue
        lines.append(f"| {issue.get('severity', '')} | {issue.get('code', '')} | {issue.get('message', '')} |")
    return "\n".join(lines) + "\n"

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from auto_alpha.portfolio.simulator.backtest import AShareTradingRules



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
            child_orders = _adapter_run_broker_read_jsonl(Path(args.child_orders_path))
            requests = build_broker_requests_from_child_orders(child_orders, prices, args.trade_date, args.batch_id, AShareTradingRules())
            adapter = SimulatedBrokerAdapter(args.store_dir, prices=prices, auto_fill=args.auto_fill)
            result = adapter.submit_orders(requests, batch_id=args.batch_id)
            _print(result.to_dict(), args.pretty)
            return 0
        if args.command == "export-file":
            child_orders = _adapter_run_broker_read_jsonl(Path(args.child_orders_path))
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
            expected = _adapter_run_broker_read_jsonl(Path(args.expected_child_orders_path)) if args.expected_child_orders_path else []
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


def _adapter_run_broker_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _print(payload: Any, pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "BrokerAdapter",
    "BrokerAdapterConfig",
    "BrokerBatchSummary",
    "BrokerFillRecord",
    "BrokerOrderEvent",
    "BrokerOrderRecord",
    "BrokerOrderRequest",
    "BrokerOrderStatus",
    "BrokerReconciliationIssue",
    "BrokerReconciliationReport",
    "BrokerStateError",
    "BrokerSubmitResult",
    "FileInstructionBrokerAdapter",
    "LocalBrokerStore",
    "SimulatedBrokerAdapter",
    "TERMINAL_STATUSES",
    "broker_fills_to_execution_fills",
    "build_broker_requests_from_child_orders",
    "execution_fills_to_broker_fills",
    "reconcile_broker_batch",
    "validate_transition",
    "write_broker_report",
]
