"""Dataclasses for broker file dry-run gateway artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class BrokerFileGatewayMode:
    dry_run = "dry_run"
    manual_handoff = "manual_handoff"
    disabled = "disabled"


class BrokerFileSchemaName:
    generic_broker_csv = "generic_broker_csv"
    generic_broker_jsonl = "generic_broker_jsonl"
    qmt_skeleton_csv = "qmt_skeleton_csv"
    custom_csv_mapping = "custom_csv_mapping"


class BrokerFileBatchStatus:
    planned = "planned"
    exported = "exported"
    handed_off = "handed_off"
    acknowledged = "acknowledged"
    partially_acknowledged = "partially_acknowledged"
    filled = "filled"
    partially_filled = "partially_filled"
    reconciled = "reconciled"
    rejected = "rejected"
    cancelled = "cancelled"
    failed = "failed"


@dataclass(frozen=True)
class BrokerFileProfile:
    profile_id: str
    profile_name: str
    schema_name: str
    field_mapping: dict[str, str]
    date_format: str = "%Y%m%d"
    price_precision: int = 4
    value_precision: int = 2
    share_unit: int = 1
    amount_unit: float = 1.0
    encoding: str = "utf-8"
    delimiter: str = ","
    required_columns: list[str] = field(default_factory=list)
    optional_columns: list[str] = field(default_factory=list)
    side_mapping: dict[str, str] = field(default_factory=lambda: {"BUY": "BUY", "SELL": "SELL"})
    status_mapping: dict[str, str] = field(default_factory=lambda: {"ACK": "ACK", "FILLED": "FILLED", "REJECTED": "REJECTED", "PARTIAL": "PARTIAL"})
    notice: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerFileBatch:
    file_batch_id: str
    production_run_id: str
    approval_id: str
    broker_batch_id: str
    trade_date: str
    account_id: str
    profile_id: str
    status: str
    created_at: str
    exported_at: str | None = None
    handed_off_at: str | None = None
    imported_at: str | None = None
    order_count: int = 0
    total_order_value: float = 0.0
    source_order_paths: dict[str, str] = field(default_factory=dict)
    outbox_paths: dict[str, str] = field(default_factory=dict)
    inbox_paths: dict[str, str] = field(default_factory=dict)
    manifest_path: str = ""
    checksum: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerFileRecord:
    client_order_id: str
    trade_date: str
    ts_code: str
    side: str
    shares: int
    price: float
    price_type: str
    order_value: float
    parent_order_id: str | None = None
    child_order_id: str | None = None
    bucket: str | None = None
    broker_batch_id: str = ""
    production_run_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerFileRoundTripIssue:
    severity: str
    code: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerFileRoundTripReport:
    file_batch_id: str
    broker_batch_id: str
    status: str
    order_count: int
    ack_count: int
    status_count: int
    fill_count: int
    reject_count: int
    missing_ack_count: int
    orphan_fill_count: int
    duplicate_fill_count: int
    unknown_status_count: int
    error_count: int
    warning_count: int
    issues: list[BrokerFileRoundTripIssue] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class BrokerFileGatewayReport:
    file_batch_id: str
    status: str
    profile: dict[str, Any]
    batch: dict[str, Any]
    manifest: dict[str, Any]
    roundtrip: dict[str, Any]
    paths: dict[str, str]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
