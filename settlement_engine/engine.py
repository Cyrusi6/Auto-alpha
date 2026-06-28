"""Settlement event generation and account-state application."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, Sequence

from .calendar import SettlementCalendar, load_settlement_profile
from .fee_tax import normalize_fee_tax_from_fill
from .lots import (
    apply_buy_fill_to_lots,
    apply_sell_fill_to_lots,
    bootstrap_lots_from_positions,
    compute_position_availability,
)
from .models import (
    CashBalanceBuckets,
    PositionLot,
    SettlementEvent,
    SettlementEventType,
    SettlementProfile,
    SettlementStatus,
)


def build_settlement_events_from_fills(
    fills: Sequence[object],
    trade_date: str,
    profile: SettlementProfile,
    calendar: SettlementCalendar,
    cost_model=None,
    account_id: str = "paper_ashare",
) -> list[SettlementEvent]:
    events: list[SettlementEvent] = []
    for fill in fills:
        payload = _payload(fill)
        status = str(payload.get("status") or "").upper()
        source_id = str(payload.get("broker_fill_id") or payload.get("child_order_id") or _source_hash(payload))
        fill_trade_date = str(payload.get("trade_date") or trade_date)
        ts_code = str(payload.get("ts_code") or "")
        side = str(payload.get("side") or "").upper()
        shares = int(payload.get("shares") or 0)
        value = float(payload.get("value") or 0.0)
        fee_tax, warnings = normalize_fee_tax_from_fill(payload, cost_model=cost_model)
        metadata = {
            "broker_order_id": payload.get("broker_order_id"),
            "broker_fill_id": payload.get("broker_fill_id"),
            "client_order_id": payload.get("client_order_id"),
            "broker_batch_id": payload.get("broker_batch_id"),
            "child_order_id": payload.get("child_order_id"),
            "parent_order_id": payload.get("parent_order_id"),
            "legacy_warnings": warnings,
        }
        if status not in {"FILLED", "PARTIAL"} or shares <= 0:
            events.append(
                _event(
                    account_id,
                    source_id,
                    "trade_fill",
                    fill_trade_date,
                    fill_trade_date,
                    fill_trade_date,
                    fill_trade_date,
                    ts_code,
                    side,
                    SettlementEventType.manual_adjustment,
                    0,
                    0.0,
                    fee_tax.to_dict(),
                    SettlementStatus.skipped,
                    str(payload.get("reason") or status.lower() or "not_filled"),
                    metadata,
                )
            )
            continue
        if side == "BUY":
            cash_date = calendar.next_trade_date(fill_trade_date, profile.buy_cash_settlement_lag_days)
            share_date = calendar.next_trade_date(fill_trade_date, profile.buy_share_available_lag_days)
            events.append(
                _event(
                    account_id,
                    source_id,
                    "trade_fill",
                    fill_trade_date,
                    cash_date,
                    cash_date,
                    cash_date,
                    ts_code,
                    side,
                    SettlementEventType.trade_buy_cash,
                    0,
                    -(value + fee_tax.total),
                    fee_tax.to_dict(),
                    metadata=metadata,
                )
            )
            events.append(
                _event(
                    account_id,
                    source_id,
                    "trade_fill",
                    fill_trade_date,
                    share_date,
                    share_date,
                    share_date,
                    ts_code,
                    side,
                    SettlementEventType.trade_buy_shares,
                    shares,
                    0.0,
                    fee_tax.to_dict(),
                    metadata={**metadata, "price": payload.get("price"), "total_cost": value + fee_tax.total},
                )
            )
        elif side == "SELL":
            share_date = calendar.next_trade_date(fill_trade_date, profile.sell_share_delivery_lag_days)
            cash_usable = calendar.next_trade_date(fill_trade_date, profile.sell_cash_usable_lag_days)
            cash_withdrawable = calendar.next_trade_date(fill_trade_date, profile.sell_cash_withdrawable_lag_days)
            events.append(
                _event(
                    account_id,
                    source_id,
                    "trade_fill",
                    fill_trade_date,
                    share_date,
                    share_date,
                    share_date,
                    ts_code,
                    side,
                    SettlementEventType.trade_sell_shares,
                    -shares,
                    0.0,
                    fee_tax.to_dict(),
                    metadata={**metadata, "proceeds": value, "fee_tax_total": fee_tax.total},
                )
            )
            events.append(
                _event(
                    account_id,
                    source_id,
                    "trade_fill",
                    fill_trade_date,
                    cash_usable,
                    cash_usable,
                    cash_withdrawable,
                    ts_code,
                    side,
                    SettlementEventType.trade_sell_cash,
                    0,
                    value - fee_tax.total,
                    fee_tax.to_dict(),
                    metadata=metadata,
                )
            )
    return events


def build_settlement_events_from_corporate_actions(
    applications_or_events: Sequence[object],
    profile: SettlementProfile,
    calendar: SettlementCalendar,
    account_id: str = "paper_ashare",
) -> list[SettlementEvent]:
    events: list[SettlementEvent] = []
    for raw in applications_or_events:
        payload = _payload(raw)
        status = str(payload.get("status") or "")
        if status and status != "APPLIED":
            continue
        ts_code = str(payload.get("ts_code") or "")
        action_id = str(payload.get("action_id") or payload.get("source_id") or _source_hash(payload))
        apply_date = str(payload.get("apply_date") or payload.get("pay_date") or payload.get("ex_date") or "")
        cash = float(payload.get("cash_amount") or payload.get("cash_div_per_share") or 0.0)
        shares_after = int(payload.get("shares_after") or 0)
        shares_before = int(payload.get("shares_before") or 0)
        share_delta = max(shares_after - shares_before, 0)
        if cash:
            settle_date = apply_date or str(payload.get("pay_date") or "")
            events.append(
                _event(
                    account_id,
                    action_id,
                    "corporate_action",
                    settle_date,
                    settle_date,
                    settle_date,
                    settle_date,
                    ts_code,
                    None,
                    SettlementEventType.corporate_action_cash,
                    0,
                    cash,
                    {},
                    metadata={"action_id": action_id},
                )
            )
        if share_delta:
            available_date = apply_date or str(payload.get("ex_date") or "")
            events.append(
                _event(
                    account_id,
                    action_id,
                    "corporate_action",
                    available_date,
                    available_date,
                    available_date,
                    available_date,
                    ts_code,
                    None,
                    SettlementEventType.corporate_action_shares,
                    share_delta,
                    0.0,
                    {},
                    metadata={"action_id": action_id},
                )
            )
    return events


def apply_settlement_events(account_state, events: Sequence[SettlementEvent | dict[str, Any]], as_of_date: str, prices=None, profile=None):
    from paper_account.models import PaperAccountState, PaperCashLedgerEntry, PaperPosition

    profile = profile or load_settlement_profile()
    existing = {_event_id(event) for event in account_state.settlement_events}
    event_payloads = [event.to_dict() if hasattr(event, "to_dict") else dict(event) for event in events]
    settlement_events = [dict(event) for event in account_state.settlement_events]
    for event in event_payloads:
        if event["settlement_event_id"] not in existing:
            settlement_events.append(event)
            existing.add(event["settlement_event_id"])

    cash = float(account_state.cash)
    positions = dict(account_state.positions)
    cash_ledger = list(account_state.cash_ledger)
    lots = _load_lots(account_state, as_of_date)
    realized_records = list(getattr(account_state, "realized_pnl_ledger", []) or [])

    settled_ids = {event["settlement_event_id"] for event in settlement_events if event.get("status") == SettlementStatus.settled}
    for event in settlement_events:
        if event.get("status") != SettlementStatus.pending:
            continue
        if str(event.get("settle_date") or event.get("available_date") or "") > as_of_date:
            continue
        event_type = str(event.get("event_type") or "")
        ts_code = str(event.get("ts_code") or "")
        shares = int(event.get("shares") or 0)
        cash_amount = float(event.get("cash_amount") or 0.0)
        source_id = str(event.get("source_id") or event.get("settlement_event_id"))
        fee_total = float((event.get("fee_tax") or {}).get("total", 0.0) or 0.0)
        if event["settlement_event_id"] in settled_ids:
            continue
        if event_type in {SettlementEventType.trade_buy_cash, SettlementEventType.trade_sell_cash, SettlementEventType.corporate_action_cash}:
            cash += cash_amount
            cash_ledger.append(PaperCashLedgerEntry(str(event.get("settle_date") or as_of_date), cash_amount, cash, f"settlement_{event_type}", ts_code or None))
        elif event_type == SettlementEventType.trade_buy_shares and shares > 0:
            total_cost = float((event.get("metadata") or {}).get("total_cost", 0.0) or 0.0)
            if total_cost <= 0:
                total_cost = shares * float((event.get("metadata") or {}).get("price", 0.0) or 0.0) + fee_total
            lots = apply_buy_fill_to_lots(
                lots,
                account_id=account_state.account_id,
                ts_code=ts_code,
                source_id=source_id,
                source_type=str(event.get("source_type") or "trade_fill"),
                trade_date=str(event.get("trade_date") or as_of_date),
                settle_date=str(event.get("settle_date") or as_of_date),
                available_date=str(event.get("available_date") or as_of_date),
                shares=shares,
                total_cost=total_cost,
            )
            positions[ts_code] = _position_from_lots(ts_code, lots, prices)
        elif event_type == SettlementEventType.trade_sell_shares and shares < 0:
            sell_shares = abs(shares)
            event_metadata = event.get("metadata") or {}
            proceeds = float(event_metadata.get("proceeds", 0.0) or 0.0)
            sell_fee_total = float(event_metadata.get("fee_tax_total", fee_total) or 0.0)
            lots, pnl = apply_sell_fill_to_lots(
                lots,
                ts_code=ts_code,
                shares=sell_shares,
                proceeds=proceeds,
                fee_tax_total=sell_fee_total,
                trade_date=str(event.get("trade_date") or as_of_date),
                sell_fill_id=source_id,
                method=profile.cost_basis_method,
            )
            realized_records.append(pnl.to_dict())
            position = _position_from_lots(ts_code, lots, prices)
            if position.shares > 0:
                positions[ts_code] = position
            else:
                positions.pop(ts_code, None)
        elif event_type == SettlementEventType.corporate_action_shares and shares > 0:
            existing_position = positions.get(ts_code)
            avg_cost = float(existing_position.avg_cost if existing_position else 0.0)
            lots = apply_buy_fill_to_lots(
                lots,
                account_id=account_state.account_id,
                ts_code=ts_code,
                source_id=source_id,
                source_type="corporate_action",
                trade_date=str(event.get("trade_date") or as_of_date),
                settle_date=str(event.get("settle_date") or as_of_date),
                available_date=str(event.get("available_date") or as_of_date),
                shares=shares,
                total_cost=0.0,
            )
            positions[ts_code] = _position_from_lots(ts_code, lots, prices, fallback_avg_cost=avg_cost)
        event["status"] = SettlementStatus.settled

    availability = compute_position_availability(lots, as_of_date)
    cash_buckets = update_cash_buckets_from_events(cash, settlement_events, as_of_date)
    positions = {key: _position_with_availability(value, availability) for key, value in positions.items()}
    updated = PaperAccountState(
        account_id=account_state.account_id,
        initial_cash=account_state.initial_cash,
        cash=float(cash),
        positions=positions,
        cash_ledger=cash_ledger,
        trade_ledger=account_state.trade_ledger,
        corporate_action_ledger=account_state.corporate_action_ledger,
        settlement_ledger=settlement_events,
        snapshots=account_state.snapshots,
        updated_at=account_state.updated_at,
        available_cash=cash_buckets.available_cash,
        withdrawable_cash=cash_buckets.withdrawable_cash,
        frozen_cash=cash_buckets.frozen_cash,
        unsettled_receivable=cash_buckets.unsettled_receivable,
        unsettled_payable=cash_buckets.unsettled_payable,
        position_lots=[lot.to_dict() for lot in lots],
        settlement_events=settlement_events,
        realized_pnl_ledger=realized_records,
        account_nav=getattr(account_state, "account_nav", []),
    )
    return updated


def settle_pending_events(account_state, as_of_date: str, prices=None, profile=None):
    return apply_settlement_events(account_state, [], as_of_date, prices=prices, profile=profile)


def precheck_orders_against_availability(account_state, orders: Sequence[object], prices=None, profile=None) -> dict[str, Any]:
    available_cash = float(account_state.available_cash if account_state.available_cash is not None else account_state.cash)
    available_by_code = {
        ts_code: int(getattr(position, "available_shares", 0) or position.shares)
        for ts_code, position in account_state.positions.items()
    }
    cash_shortfall = 0.0
    share_violations: list[dict[str, Any]] = []
    rejected = 0
    buy_value = 0.0
    for order in orders:
        payload = _payload(order)
        side = str(payload.get("side") or "").upper()
        value = float(payload.get("order_value") or payload.get("value") or 0.0)
        ts_code = str(payload.get("ts_code") or "")
        if side == "BUY":
            buy_value += value
        elif side == "SELL":
            price = float((prices or {}).get(ts_code, 0.0) or payload.get("price") or 0.0)
            requested = int(value / price) if price > 0 else 0
            if requested > available_by_code.get(ts_code, 0):
                rejected += 1
                share_violations.append({"ts_code": ts_code, "requested_shares": requested, "available_shares": available_by_code.get(ts_code, 0)})
    if buy_value > available_cash:
        cash_shortfall = buy_value - available_cash
        rejected += 1
    return {
        "available_cash": float(available_cash),
        "buy_order_value": float(buy_value),
        "cash_shortfall": float(cash_shortfall),
        "available_share_violations": share_violations,
        "unavailable_share_count": int(len(share_violations)),
        "precheck_rejected_order_count": int(rejected),
    }


def freeze_for_orders(account_state, orders, prices=None, batch_id: str | None = None):
    del orders, prices, batch_id
    return account_state


def release_frozen_for_rejected_fills(account_state, fills):
    del fills
    return account_state


def update_cash_buckets_from_events(cash: float, events: Sequence[dict[str, Any]], as_of_date: str) -> CashBalanceBuckets:
    receivable = 0.0
    payable = 0.0
    for event in events:
        if event.get("status") == SettlementStatus.settled:
            continue
        amount = float(event.get("cash_amount") or 0.0)
        if not amount:
            continue
        if str(event.get("available_date") or "") > as_of_date:
            if amount > 0:
                receivable += amount
            else:
                payable += abs(amount)
    available = cash - payable
    withdrawable = available
    return CashBalanceBuckets(
        trade_date=as_of_date,
        total_cash=float(cash),
        available_cash=float(available),
        withdrawable_cash=float(withdrawable),
        frozen_cash=0.0,
        unsettled_receivable=float(receivable),
        unsettled_payable=float(payable),
        reserved_buy_cash=0.0,
    )


def update_cash_buckets(account_state, as_of_date: str) -> CashBalanceBuckets:
    return update_cash_buckets_from_events(float(account_state.cash), list(account_state.settlement_events), as_of_date)


def update_position_availability(account_state, as_of_date: str):
    return compute_position_availability(_load_lots(account_state, as_of_date), as_of_date)


def compute_realized_pnl_from_fills(account_state):
    return list(getattr(account_state, "realized_pnl_ledger", []) or [])


def _event(
    account_id: str,
    source_id: str,
    source_type: str,
    trade_date: str,
    settle_date: str,
    available_date: str,
    withdrawable_date: str,
    ts_code: str | None,
    side: str | None,
    event_type: str,
    shares: int = 0,
    cash_amount: float = 0.0,
    fee_tax: dict[str, float] | None = None,
    status: str = SettlementStatus.pending,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> SettlementEvent:
    event_id = "se_" + hashlib.sha256(
        "|".join([account_id, source_id, event_type, str(ts_code or ""), str(settle_date)]).encode("utf-8")
    ).hexdigest()[:24]
    return SettlementEvent(
        settlement_event_id=event_id,
        account_id=account_id,
        source_type=source_type,
        source_id=source_id,
        trade_date=trade_date,
        settle_date=settle_date,
        available_date=available_date,
        withdrawable_date=withdrawable_date,
        ts_code=ts_code,
        side=side,
        event_type=event_type,
        shares=int(shares),
        cash_amount=float(cash_amount),
        fee_tax=fee_tax or {},
        status=status,
        reason=reason,
        metadata=metadata or {},
    )


def _load_lots(state, trade_date: str) -> list[PositionLot]:
    raw = getattr(state, "position_lots", []) or []
    if raw:
        return [PositionLot(**dict(item)) for item in raw]
    return bootstrap_lots_from_positions(state, trade_date)


def _position_from_lots(ts_code: str, lots: list[PositionLot], prices=None, fallback_avg_cost: float = 0.0):
    from paper_account.models import PaperPosition

    relevant = [lot for lot in lots if lot.ts_code == ts_code and lot.shares_remaining > 0]
    shares = sum(lot.shares_remaining for lot in relevant)
    total_cost = sum(lot.shares_remaining * lot.unit_cost for lot in relevant)
    avg_cost = total_cost / max(shares, 1) if shares else fallback_avg_cost
    price = float((prices or {}).get(ts_code, avg_cost) or avg_cost)
    market_value = shares * price
    return PaperPosition(
        ts_code=ts_code,
        shares=int(shares),
        avg_cost=float(avg_cost),
        market_price=float(price),
        market_value=float(market_value),
        unrealized_pnl=float(market_value - total_cost),
        available_shares=int(shares),
        unsettled_buy_shares=0,
        lot_count=len(relevant),
    )


def _position_with_availability(position, availability):
    match = next((record for record in availability if record.ts_code == position.ts_code), None)
    if match is None:
        return position
    return replace(
        position,
        available_shares=match.available_shares,
        frozen_shares=match.frozen_shares,
        unsettled_buy_shares=match.unsettled_buy_shares,
        pending_sell_shares=match.pending_sell_shares,
        lot_count=match.lot_count,
    )


def _event_id(event: dict[str, Any]) -> str:
    return str(event.get("settlement_event_id") or "")


def _payload(record: object) -> dict[str, Any]:
    if hasattr(record, "to_dict"):
        return dict(record.to_dict())
    if hasattr(record, "__dataclass_fields__"):
        return {field: getattr(record, field) for field in record.__dataclass_fields__}
    return dict(record)


def _source_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256("|".join(str(payload.get(key) or "") for key in sorted(payload)).encode("utf-8")).hexdigest()[:20]
