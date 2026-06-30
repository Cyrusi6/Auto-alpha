"""Models for read-only broker mirror snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class BrokerReadonlySnapshotStatus:
    success = "success"
    warning = "warning"
    failed = "failed"
    skipped = "skipped"


@dataclass(frozen=True)
class BrokerReadonlyCash:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    cash_balance: float
    available_cash: float = 0.0
    withdrawable_cash: float = 0.0
    frozen_cash: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerReadonlyPosition:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    ts_code: str
    position_shares: int
    available_shares: int = 0
    cost_basis: float = 0.0
    market_value: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerReadonlyOrder:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    external_order_id: str
    broker_order_id: str = ""
    client_order_id: str = ""
    ts_code: str = ""
    side: str = ""
    price: float = 0.0
    shares: int = 0
    value: float = 0.0
    status: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerReadonlyFill:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    external_fill_id: str
    broker_fill_id: str = ""
    broker_order_id: str = ""
    client_order_id: str = ""
    ts_code: str = ""
    side: str = ""
    price: float = 0.0
    shares: int = 0
    value: float = 0.0
    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0
    total_fee: float = 0.0
    status: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerReadonlyStatement:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    statement_id: str
    cash_balance: float = 0.0
    position_count: int = 0
    fill_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerReadonlyMirrorIssue:
    severity: str
    code: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerReadonlySnapshot:
    snapshot_id: str
    connectivity_session_id: str
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    status: str
    cash: dict[str, Any]
    positions: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    fills: list[dict[str, Any]]
    statements: list[dict[str, Any]]
    source_hash: str
    created_at: str
    issues: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerReadonlyMirrorReport:
    report_id: str
    created_at: str
    status: str
    snapshot: dict[str, Any]
    summary: dict[str, Any]
    paths: dict[str, str]
    issues: list[dict[str, Any]] = field(default_factory=list)
    real_submit_supported: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

