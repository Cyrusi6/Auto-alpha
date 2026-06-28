"""Dataclasses for local corporate action artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class CorporateActionType(str, Enum):
    CASH_DIVIDEND = "cash_dividend"
    STOCK_BONUS = "stock_bonus"
    STOCK_TRANSFER = "stock_transfer"
    COMBINED_DISTRIBUTION = "combined_distribution"
    PROPOSAL_ONLY = "proposal_only"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CorporateActionEvent:
    action_id: str
    ts_code: str
    action_type: str
    status: str
    end_date: str | None
    ann_date: str | None
    imp_ann_date: str | None
    record_date: str | None
    ex_date: str | None
    pay_date: str | None
    div_listdate: str | None
    cash_div_per_share: float
    cash_div_tax_per_share: float
    stock_bonus_ratio: float
    stock_transfer_ratio: float
    stock_distribution_ratio: float
    availability_date: str | None
    effective_date: str | None
    source_record: dict[str, Any] = field(default_factory=dict)
    unit_assumption: str = "per_share_from_provider"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CorporateActionApplication:
    application_id: str
    account_id: str
    action_id: str
    ts_code: str
    event_type: str
    eligibility_date: str | None
    apply_date: str
    shares_before: int
    shares_after: int
    cash_amount: float
    tax_amount: float
    avg_cost_before: float
    avg_cost_after: float
    status: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CorporateActionLedgerEntry:
    apply_date: str
    action_id: str
    ts_code: str
    event_type: str
    shares_before: int
    shares_after: int
    cash_amount: float
    tax_amount: float
    avg_cost_before: float
    avg_cost_after: float
    status: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TotalReturnSeriesRecord:
    trade_date: str
    ts_code: str
    raw_close: float
    adjusted_close: float
    cash_dividend: float
    stock_distribution_ratio: float
    total_return_price: float
    total_return: float
    action_flag: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdjustmentFactorReconciliationIssue:
    severity: str
    code: str
    message: str
    ts_code: str | None = None
    trade_date: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CorporateActionValidationReport:
    event_count: int
    implemented_action_count: int
    proposal_action_count: int
    warning_count: int
    error_count: int
    issues: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CorporateActionReport:
    created_at: str
    data_dir: str
    event_count: int
    implemented_action_count: int
    proposal_action_count: int
    cash_dividend_event_count: int
    stock_distribution_event_count: int
    combined_event_count: int
    cash_dividend_amount_per_share: float
    stock_distribution_ratio_sum: float
    unprocessed_corporate_action_count: int
    corporate_action_warning_count: int
    corporate_action_error_count: int
    total_return_mode: str
    adjustment_reconciliation_warning_count: int
    adjustment_reconciliation_error_count: int
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
