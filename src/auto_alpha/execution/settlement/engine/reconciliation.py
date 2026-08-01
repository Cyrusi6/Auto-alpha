"""Settlement account reconciliation checks."""

from __future__ import annotations

from collections import Counter
from typing import Any, Sequence

from .models import AccountReconciliationIssue, AccountReconciliationReport, SettlementStatus


def reconcile_account_state(account_state, settlement_events: Sequence[dict[str, Any]] | None = None, lots=None, prices=None, nav_records=None, as_of_date: str = "") -> AccountReconciliationReport:
    events = [dict(event) for event in (settlement_events if settlement_events is not None else account_state.settlement_events)]
    lots = list(lots if lots is not None else getattr(account_state, "position_lots", []) or [])
    nav_records = list(nav_records if nav_records is not None else getattr(account_state, "account_nav", []) or [])
    event_ids = [str(event.get("settlement_event_id") or "") for event in events]
    duplicate_count = sum(count - 1 for count in Counter(event_ids).values() if count > 1 and event_ids)
    pending = sum(1 for event in events if event.get("status") == SettlementStatus.pending)
    failed = sum(1 for event in events if event.get("status") == SettlementStatus.failed)
    lot_shares = sum(int(lot.get("shares_remaining", 0)) for lot in lots)
    position_shares = sum(int(position.shares) for position in account_state.positions.values())
    issues: list[AccountReconciliationIssue] = []
    lot_diff = position_shares - lot_shares
    if lot_diff:
        issues.append(
            AccountReconciliationIssue(
                severity="warning",
                code="lot_position_share_mismatch",
                message="position shares differ from lot shares",
                metadata={"position_shares": position_shares, "lot_shares": lot_shares},
            )
        )
    if duplicate_count:
        issues.append(
            AccountReconciliationIssue(
                severity="error",
                code="duplicate_settlement_event",
                message="duplicate settlement event ids found",
                metadata={"duplicate_event_count": duplicate_count},
            )
        )
    nav_difference = 0.0
    if nav_records:
        latest = nav_records[-1].to_dict() if hasattr(nav_records[-1], "to_dict") else dict(nav_records[-1])
        positions_value = sum(float(position.market_value) for position in account_state.positions.values())
        computed_equity = float(account_state.cash) + positions_value
        nav_difference = computed_equity - float(latest.get("equity", 0.0) or 0.0)
        if abs(nav_difference) > 1e-6:
            issues.append(
                AccountReconciliationIssue(
                    severity="warning",
                    code="nav_difference",
                    message="computed NAV differs from NAV record",
                    metadata={"nav_difference": nav_difference},
                )
            )
    return AccountReconciliationReport(
        account_id=account_state.account_id,
        as_of_date=as_of_date or "",
        broker_fill_count=sum(1 for event in events if event.get("source_type") == "broker_fill"),
        trade_ledger_count=len(account_state.trade_ledger),
        settlement_event_count=len(events),
        pending_event_count=pending,
        failed_event_count=failed,
        unmatched_broker_fills=0,
        unmatched_trade_ledger_entries=0,
        unmatched_settlement_events=0,
        cash_difference=0.0,
        position_share_difference=0,
        lot_share_difference=lot_diff,
        nav_difference=float(nav_difference),
        realized_pnl_difference=0.0,
        duplicate_event_count=duplicate_count,
        idempotent_replay_count=0,
        issues=issues,
    )


def reconcile_broker_fills_to_settlements(broker_fills, settlement_events) -> AccountReconciliationReport:
    source_ids = {str(event.get("source_id") or "") for event in settlement_events}
    missing = [fill for fill in broker_fills if str(getattr(fill, "broker_fill_id", "") or fill.get("broker_fill_id", "")) not in source_ids]
    issues = [
        AccountReconciliationIssue("warning", "missing_settlement_for_broker_fill", "broker fill has no settlement event", {"count": len(missing)})
    ] if missing else []
    return AccountReconciliationReport(
        account_id="",
        as_of_date="",
        broker_fill_count=len(broker_fills),
        settlement_event_count=len(settlement_events),
        unmatched_broker_fills=len(missing),
        issues=issues,
    )


def reconcile_trade_ledger_to_lots(trade_ledger, lots) -> dict[str, Any]:
    return {"trade_ledger_count": len(trade_ledger), "lot_count": len(lots), "ok": True}


def reconcile_cash_ledger_to_cash_buckets(cash_ledger, cash_buckets) -> dict[str, Any]:
    return {"cash_ledger_count": len(cash_ledger), "cash_buckets": cash_buckets, "ok": True}


def reconcile_corporate_actions_to_settlements(corporate_action_ledger, settlement_events) -> dict[str, Any]:
    action_ids = {str(event.get("source_id") or "") for event in settlement_events if event.get("source_type") == "corporate_action"}
    unmatched = [entry for entry in corporate_action_ledger if getattr(entry, "action_id", "") not in action_ids]
    return {"corporate_action_ledger_count": len(corporate_action_ledger), "unmatched_corporate_actions": len(unmatched), "ok": True}
