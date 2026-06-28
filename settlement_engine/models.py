"""Settlement-aware paper accounting models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class SettlementStatus:
    pending = "pending"
    settled = "settled"
    skipped = "skipped"
    cancelled = "cancelled"
    failed = "failed"


class SettlementEventType:
    trade_buy_cash = "trade_buy_cash"
    trade_buy_shares = "trade_buy_shares"
    trade_sell_cash = "trade_sell_cash"
    trade_sell_shares = "trade_sell_shares"
    fee_tax = "fee_tax"
    cash_dividend = "cash_dividend"
    stock_distribution = "stock_distribution"
    corporate_action_cash = "corporate_action_cash"
    corporate_action_shares = "corporate_action_shares"
    mark_to_market = "mark_to_market"
    manual_adjustment = "manual_adjustment"


@dataclass(frozen=True)
class SettlementProfile:
    profile_name: str = "cn_ashare_paper_default"
    buy_cash_settlement_lag_days: int = 0
    sell_cash_usable_lag_days: int = 1
    sell_cash_withdrawable_lag_days: int = 1
    buy_share_available_lag_days: int = 1
    sell_share_delivery_lag_days: int = 0
    corporate_cash_lag_mode: str = "pay_date"
    corporate_share_lag_mode: str = "ex_date"
    allow_same_day_sell_proceeds_for_buy: bool = False
    allow_unsettled_cash_for_buy: bool = False
    allow_unsettled_shares_for_sell: bool = False
    cost_basis_method: str = "average"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeeTaxBreakdown:
    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0
    slippage: float = 0.0
    market_impact: float = 0.0
    other_fee: float = 0.0
    total: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {key: float(value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class SettlementEvent:
    settlement_event_id: str
    account_id: str
    source_type: str
    source_id: str
    trade_date: str
    settle_date: str
    available_date: str
    withdrawable_date: str
    ts_code: str | None
    side: str | None
    event_type: str
    shares: int = 0
    cash_amount: float = 0.0
    fee_tax: dict[str, float] = field(default_factory=dict)
    status: str = SettlementStatus.pending
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PositionLot:
    lot_id: str
    account_id: str
    ts_code: str
    source_id: str
    source_type: str
    open_date: str
    settle_date: str
    available_date: str
    shares_original: int
    shares_remaining: int
    unit_cost: float
    total_cost: float
    realized_pnl: float = 0.0
    status: str = "open"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PositionAvailability:
    ts_code: str
    trade_date: str
    total_shares: int
    available_shares: int
    frozen_shares: int
    unsettled_buy_shares: int
    pending_sell_shares: int
    lot_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CashBalanceBuckets:
    trade_date: str
    total_cash: float
    available_cash: float
    withdrawable_cash: float
    frozen_cash: float = 0.0
    unsettled_receivable: float = 0.0
    unsettled_payable: float = 0.0
    reserved_buy_cash: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RealizedPnlRecord:
    trade_date: str
    ts_code: str
    sell_fill_id: str
    shares: int
    proceeds: float
    allocated_cost_basis: float
    fee_tax_total: float
    realized_pnl: float
    cost_basis_method: str
    lot_allocations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AccountNavRecord:
    trade_date: str
    equity: float
    cash: float
    positions_value: float
    unsettled_cash: float
    frozen_cash: float
    realized_pnl: float
    unrealized_pnl: float
    fees: float
    taxes: float
    corporate_action_cash: float
    daily_return: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SettlementBatchResult:
    account_id: str
    as_of_date: str
    profile: dict[str, Any]
    events: list[SettlementEvent] = field(default_factory=list)
    cash_buckets: CashBalanceBuckets | None = None
    position_availability: list[PositionAvailability] = field(default_factory=list)
    realized_pnl: list[RealizedPnlRecord] = field(default_factory=list)
    nav_records: list[AccountNavRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "as_of_date": self.as_of_date,
            "profile": self.profile,
            "events": [event.to_dict() for event in self.events],
            "cash_buckets": self.cash_buckets.to_dict() if self.cash_buckets else None,
            "position_availability": [record.to_dict() for record in self.position_availability],
            "realized_pnl": [record.to_dict() for record in self.realized_pnl],
            "nav_records": [record.to_dict() for record in self.nav_records],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class AccountReconciliationIssue:
    severity: str
    code: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AccountReconciliationReport:
    account_id: str
    as_of_date: str
    broker_fill_count: int = 0
    trade_ledger_count: int = 0
    settlement_event_count: int = 0
    pending_event_count: int = 0
    failed_event_count: int = 0
    unmatched_broker_fills: int = 0
    unmatched_trade_ledger_entries: int = 0
    unmatched_settlement_events: int = 0
    cash_difference: float = 0.0
    position_share_difference: int = 0
    lot_share_difference: int = 0
    nav_difference: float = 0.0
    realized_pnl_difference: float = 0.0
    duplicate_event_count: int = 0
    idempotent_replay_count: int = 0
    issues: list[AccountReconciliationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        issue_payloads = [issue.to_dict() for issue in self.issues]
        return {
            **asdict(self),
            "issues": issue_payloads,
            "error_count": sum(1 for issue in self.issues if issue.severity in {"error", "blocker"}),
            "warning_count": sum(1 for issue in self.issues if issue.severity == "warning"),
        }


@dataclass(frozen=True)
class SettlementReport:
    account_id: str
    as_of_date: str
    settlement_aware: bool
    settlement_profile: str
    pending_settlement_event_count: int
    failed_settlement_event_count: int
    cash_buckets: dict[str, Any]
    position_count: int
    position_lot_count: int
    realized_pnl: float
    unrealized_pnl: float
    nav_difference: float
    fee_tax_total: float
    reconciliation_error_count: int
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
