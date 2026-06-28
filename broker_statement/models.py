"""Generic broker statement dataclasses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BrokerStatementSchema:
    schema_name: str
    field_mapping: dict[str, dict[str, str]] = field(default_factory=dict)
    required_files: list[str] = field(default_factory=list)
    optional_files: list[str] = field(default_factory=list)
    date_format: str = "YYYYMMDD"
    amount_unit: str = "yuan"
    price_unit: str = "yuan"
    shares_unit: str = "share"
    notice: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerStatementManifest:
    statement_id: str
    account_id: str
    broker_name: str
    schema_name: str
    trade_date: str
    as_of_date: str
    source_dir: str
    source_file_hashes: dict[str, dict[str, Any]]
    imported_at: str
    record_counts: dict[str, int]
    parse_issue_count: int = 0
    warning_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerStatementParseIssue:
    severity: str
    code: str
    message: str
    file_name: str = ""
    line_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerStatementValidationReport:
    statement_id: str
    status: str
    issue_count: int
    error_count: int
    warning_count: int
    issues: list[BrokerStatementParseIssue] = field(default_factory=list)
    dataset_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement_id": self.statement_id,
            "status": self.status,
            "issue_count": int(self.issue_count),
            "error_count": int(self.error_count),
            "warning_count": int(self.warning_count),
            "issues": [issue.to_dict() for issue in self.issues],
            "dataset_counts": dict(self.dataset_counts),
        }


@dataclass(frozen=True)
class BrokerStatementImportResult:
    statement_id: str
    status: str
    manifest: BrokerStatementManifest
    validation: BrokerStatementValidationReport
    paths: dict[str, str]
    synthetic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement_id": self.statement_id,
            "status": self.status,
            "manifest": self.manifest.to_dict(),
            "validation": self.validation.to_dict(),
            "paths": dict(self.paths),
            "synthetic": bool(self.synthetic),
        }


@dataclass(frozen=True)
class ExternalBrokerOrder:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    external_order_id: str = ""
    broker_order_id: str = ""
    client_order_id: str = ""
    ts_code: str = ""
    side: str = ""
    price: float = 0.0
    shares: int = 0
    value: float = 0.0
    status: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerTrade:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    external_trade_id: str = ""
    external_order_id: str = ""
    broker_order_id: str = ""
    client_order_id: str = ""
    ts_code: str = ""
    side: str = ""
    price: float = 0.0
    shares: int = 0
    value: float = 0.0
    total_fee: float = 0.0
    status: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerFill:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    external_fill_id: str = ""
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
    slippage: float = 0.0
    market_impact: float = 0.0
    other_fee: float = 0.0
    total_fee: float = 0.0
    status: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerPosition:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    ts_code: str
    position_shares: int
    available_shares: int = 0
    cost_basis: float = 0.0
    market_value: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerCashBalance:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    cash_balance: float
    available_cash: float = 0.0
    withdrawable_cash: float = 0.0
    frozen_cash: float = 0.0
    unsettled_receivable: float = 0.0
    unsettled_payable: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerSettlementItem:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    external_settlement_id: str = ""
    source_id: str = ""
    ts_code: str = ""
    event_type: str = ""
    settlement_date: str = ""
    available_date: str = ""
    cash_amount: float = 0.0
    shares: int = 0
    status: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerCorporateActionItem:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    external_action_id: str = ""
    action_id: str = ""
    ts_code: str = ""
    event_type: str = ""
    cash_amount: float = 0.0
    shares: int = 0
    status: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerAccountSnapshot:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    cash_balance: float
    positions_value: float
    equity: float
    position_count: int
    fill_count: int
    settlement_count: int = 0
    corporate_action_count: int = 0
    synthetic: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
