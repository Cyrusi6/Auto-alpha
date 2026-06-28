"""Settlement-aware local paper accounting."""

from .calendar import SettlementCalendar, load_settlement_profile
from .engine import (
    apply_settlement_events,
    build_settlement_events_from_corporate_actions,
    build_settlement_events_from_fills,
    freeze_for_orders,
    precheck_orders_against_availability,
    release_frozen_for_rejected_fills,
    settle_pending_events,
    update_cash_buckets,
    update_position_availability,
)
from .fee_tax import estimate_fee_tax, normalize_fee_tax_from_fill, write_fee_tax_report
from .lots import allocate_sell_lots
from .models import (
    AccountNavRecord,
    AccountReconciliationIssue,
    AccountReconciliationReport,
    CashBalanceBuckets,
    FeeTaxBreakdown,
    PositionAvailability,
    PositionLot,
    RealizedPnlRecord,
    SettlementBatchResult,
    SettlementEvent,
    SettlementEventType,
    SettlementProfile,
    SettlementReport,
    SettlementStatus,
)
from .report import write_settlement_report

__all__ = [
    "AccountNavRecord",
    "AccountReconciliationIssue",
    "AccountReconciliationReport",
    "CashBalanceBuckets",
    "FeeTaxBreakdown",
    "PositionAvailability",
    "PositionLot",
    "RealizedPnlRecord",
    "SettlementBatchResult",
    "SettlementCalendar",
    "SettlementEvent",
    "SettlementEventType",
    "SettlementProfile",
    "SettlementReport",
    "SettlementStatus",
    "apply_settlement_events",
    "allocate_sell_lots",
    "build_settlement_events_from_corporate_actions",
    "build_settlement_events_from_fills",
    "estimate_fee_tax",
    "freeze_for_orders",
    "load_settlement_profile",
    "normalize_fee_tax_from_fill",
    "precheck_orders_against_availability",
    "release_frozen_for_rejected_fills",
    "settle_pending_events",
    "update_cash_buckets",
    "update_position_availability",
    "write_fee_tax_report",
    "write_settlement_report",
]
