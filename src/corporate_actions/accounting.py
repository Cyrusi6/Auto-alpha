"""Apply corporate actions to local paper-account state."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Sequence

from paper_account.models import PaperAccountState, PaperCashLedgerEntry, PaperCorporateActionLedgerEntry, PaperPosition

from .models import CorporateActionApplication, CorporateActionEvent
from .schedule import eligible_events_for_account


def apply_corporate_actions_to_positions(
    state: PaperAccountState,
    events: Sequence[CorporateActionEvent],
    trade_date: str,
    prices: dict[str, float] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[PaperAccountState, list[CorporateActionApplication]]:
    cfg = config or {}
    mode = str(cfg.get("application_date_mode") or "pay_date")
    tax_rate = float(cfg.get("tax_rate") or 0.0)
    selected = eligible_events_for_account(events, trade_date, mode=mode)
    applied_ids = {
        str(entry.metadata.get("application_id") or "")
        for entry in getattr(state, "corporate_action_ledger", [])
    }
    positions = dict(state.positions)
    cash = float(state.cash)
    cash_ledger = list(state.cash_ledger)
    action_ledger = list(getattr(state, "corporate_action_ledger", []))
    settlement_ledger = list(getattr(state, "settlement_ledger", []))
    applications: list[CorporateActionApplication] = []

    for event in selected:
        if event.action_type == "proposal_only":
            applications.append(_application(state.account_id, event, trade_date, "proposal", 0, 0, 0.0, 0.0, "SKIPPED", "proposal_only"))
            continue
        position = positions.get(event.ts_code)
        if position is None or position.shares <= 0:
            applications.append(_application(state.account_id, event, trade_date, "eligibility", 0, 0, 0.0, 0.0, "SKIPPED", "no_eligible_position"))
            continue

        if event.cash_div_per_share > 0:
            application = _application(
                state.account_id,
                event,
                trade_date,
                "cash_dividend",
                position.shares,
                position.shares,
                cash_amount=position.shares * event.cash_div_per_share,
                tax_amount=position.shares * event.cash_div_per_share * tax_rate,
                status="APPLIED",
                reason="corporate_action_cash_dividend",
                avg_cost_before=position.avg_cost,
                avg_cost_after=position.avg_cost,
            )
            if application.application_id not in applied_ids:
                net_cash = application.cash_amount - application.tax_amount
                cash += net_cash
                cash_ledger.append(
                    PaperCashLedgerEntry(
                        trade_date=trade_date,
                        amount=float(net_cash),
                        balance=float(cash),
                        reason="corporate_action_cash_dividend",
                        ts_code=event.ts_code,
                    )
                )
                action_ledger.append(_ledger_entry(application))
                settlement_ledger.append({"application_id": application.application_id, "cash_amount": float(net_cash), "trade_date": trade_date})
                applied_ids.add(application.application_id)
            applications.append(application)

        if event.stock_distribution_ratio > 0:
            shares_before = int(position.shares)
            raw_after = shares_before * (1.0 + event.stock_distribution_ratio)
            shares_after = int(math.floor(raw_after))
            fractional = max(0.0, raw_after - shares_after)
            avg_cost_after = (position.avg_cost * shares_before / shares_after) if shares_after > 0 else position.avg_cost
            application = _application(
                state.account_id,
                event,
                trade_date,
                "stock_distribution",
                shares_before,
                shares_after,
                cash_amount=0.0,
                tax_amount=0.0,
                status="APPLIED",
                reason="corporate_action_stock_distribution",
                avg_cost_before=position.avg_cost,
                avg_cost_after=avg_cost_after,
                metadata={"fractional_shares": fractional},
            )
            if application.application_id not in applied_ids:
                price = float((prices or {}).get(event.ts_code, position.market_price or position.avg_cost))
                positions[event.ts_code] = PaperPosition(
                    ts_code=event.ts_code,
                    shares=shares_after,
                    avg_cost=float(avg_cost_after),
                    market_price=price,
                    market_value=float(shares_after * price),
                    unrealized_pnl=float(shares_after * (price - avg_cost_after)),
                )
                action_ledger.append(_ledger_entry(application))
                settlement_ledger.append({"application_id": application.application_id, "shares_delta": shares_after - shares_before, "trade_date": trade_date})
                applied_ids.add(application.application_id)
            applications.append(application)

    updated = replace(
        state,
        cash=float(cash),
        positions=positions,
        cash_ledger=cash_ledger,
        corporate_action_ledger=action_ledger,
        settlement_ledger=settlement_ledger,
    )
    return updated, applications


def _application(
    account_id: str,
    event: CorporateActionEvent,
    apply_date: str,
    event_type: str,
    shares_before: int,
    shares_after: int,
    cash_amount: float,
    tax_amount: float,
    status: str,
    reason: str,
    avg_cost_before: float = 0.0,
    avg_cost_after: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> CorporateActionApplication:
    application_id = f"{account_id}_{event.action_id}_{event.ts_code}_{event_type}_{apply_date}"
    return CorporateActionApplication(
        application_id=application_id,
        account_id=account_id,
        action_id=event.action_id,
        ts_code=event.ts_code,
        event_type=event_type,
        eligibility_date=event.record_date,
        apply_date=apply_date,
        shares_before=int(shares_before),
        shares_after=int(shares_after),
        cash_amount=float(cash_amount),
        tax_amount=float(tax_amount),
        avg_cost_before=float(avg_cost_before),
        avg_cost_after=float(avg_cost_after),
        status=status,
        reason=reason,
        metadata=metadata or {},
    )


def _ledger_entry(application: CorporateActionApplication) -> PaperCorporateActionLedgerEntry:
    return PaperCorporateActionLedgerEntry(
        apply_date=application.apply_date,
        action_id=application.action_id,
        ts_code=application.ts_code,
        event_type=application.event_type,
        shares_before=application.shares_before,
        shares_after=application.shares_after,
        cash_amount=application.cash_amount,
        tax_amount=application.tax_amount,
        avg_cost_before=application.avg_cost_before,
        avg_cost_after=application.avg_cost_after,
        status=application.status,
        reason=application.reason,
        metadata={**application.metadata, "application_id": application.application_id},
    )
