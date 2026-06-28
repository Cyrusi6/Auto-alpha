"""EOD reconciliation dataclasses and constants."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ReconciliationSeverity:
    info = "info"
    warning = "warning"
    error = "error"
    blocker = "blocker"


class ReconciliationBreakType:
    missing_external_fill = "missing_external_fill"
    orphan_external_fill = "orphan_external_fill"
    fill_quantity_mismatch = "fill_quantity_mismatch"
    fill_price_mismatch = "fill_price_mismatch"
    fill_value_mismatch = "fill_value_mismatch"
    fee_tax_mismatch = "fee_tax_mismatch"
    missing_internal_order = "missing_internal_order"
    order_status_mismatch = "order_status_mismatch"
    cash_balance_mismatch = "cash_balance_mismatch"
    available_cash_mismatch = "available_cash_mismatch"
    position_share_mismatch = "position_share_mismatch"
    available_share_mismatch = "available_share_mismatch"
    lot_cost_mismatch = "lot_cost_mismatch"
    settlement_event_mismatch = "settlement_event_mismatch"
    nav_mismatch = "nav_mismatch"
    corporate_action_mismatch = "corporate_action_mismatch"
    duplicate_external_id = "duplicate_external_id"
    stale_statement = "stale_statement"
    schema_parse_error = "schema_parse_error"
    materiality_exceeded = "materiality_exceeded"


@dataclass(frozen=True)
class ReconciliationMaterialityConfig:
    cash_abs_tolerance: float = 0.01
    position_share_tolerance: int = 0
    fill_value_abs_tolerance: float = 0.01
    fee_abs_tolerance: float = 0.01
    nav_abs_tolerance: float = 0.01
    stale_statement_max_days: int = 1
    blocker_on_missing_cash_statement: bool = False
    blocker_on_missing_position_statement: bool = False
    blocker_on_unmatched_fill: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReconciliationBreak:
    break_id: str
    break_type: str
    severity: str
    message: str
    account_id: str
    ts_code: str | None = None
    external_id: str | None = None
    internal_id: str | None = None
    external_value: float | None = None
    internal_value: float | None = None
    difference: float = 0.0
    material: bool = False
    resolved: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalAccountMirror:
    statement_id: str
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    synthetic: bool
    cash: dict[str, Any] = field(default_factory=dict)
    positions: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    settlements: list[dict[str, Any]] = field(default_factory=list)
    corporate_actions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EodReconciliationReport:
    statement_id: str
    account_id: str
    trade_date: str
    as_of_date: str
    status: str
    summary: dict[str, Any]
    breaks: list[ReconciliationBreak] = field(default_factory=list)
    materiality: dict[str, Any] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement_id": self.statement_id,
            "account_id": self.account_id,
            "trade_date": self.trade_date,
            "as_of_date": self.as_of_date,
            "status": self.status,
            "summary": dict(self.summary),
            "breaks": [item.to_dict() for item in self.breaks],
            "materiality": dict(self.materiality),
            "paths": dict(self.paths),
        }


@dataclass(frozen=True)
class AdjustmentProposal:
    adjustment_id: str
    break_id: str
    account_id: str
    adjustment_type: str
    ts_code: str | None = None
    cash_amount: float = 0.0
    share_delta: int = 0
    cost_basis_delta: float = 0.0
    reason: str = ""
    severity: str = "warning"
    requires_approval: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdjustmentProposalBatch:
    adjustment_batch_id: str
    account_id: str
    trade_date: str
    as_of_date: str
    proposals: list[AdjustmentProposal]
    status: str = "pending_approval"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adjustment_batch_id": self.adjustment_batch_id,
            "account_id": self.account_id,
            "trade_date": self.trade_date,
            "as_of_date": self.as_of_date,
            "proposals": [item.to_dict() for item in self.proposals],
            "status": self.status,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AdjustmentLedgerEntry:
    adjustment_id: str
    approval_id: str
    account_id: str
    trade_date: str
    adjustment_type: str
    ts_code: str | None = None
    cash_amount: float = 0.0
    share_delta: int = 0
    cost_basis_delta: float = 0.0
    reason: str = ""
    applied_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdjustmentApplicationResult:
    approval_id: str
    account_id: str
    trade_date: str
    applied_count: int
    skipped_duplicate_count: int
    ledger_entries: list[AdjustmentLedgerEntry] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "account_id": self.account_id,
            "trade_date": self.trade_date,
            "applied_count": int(self.applied_count),
            "skipped_duplicate_count": int(self.skipped_duplicate_count),
            "ledger_entries": [entry.to_dict() for entry in self.ledger_entries],
            "paths": dict(self.paths),
        }
