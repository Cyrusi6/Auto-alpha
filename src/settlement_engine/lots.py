"""Position lot and availability calculations."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

from .models import PositionAvailability, PositionLot, RealizedPnlRecord


def bootstrap_lots_from_positions(state, trade_date: str) -> list[PositionLot]:
    lots: list[PositionLot] = []
    for ts_code, position in state.positions.items():
        if int(position.shares) <= 0:
            continue
        lots.append(
            PositionLot(
                lot_id=f"lot_bootstrap_{ts_code}",
                account_id=state.account_id,
                ts_code=ts_code,
                source_id="bootstrap_position",
                source_type="bootstrap_position",
                open_date=trade_date,
                settle_date=trade_date,
                available_date=trade_date,
                shares_original=int(position.shares),
                shares_remaining=int(position.shares),
                unit_cost=float(position.avg_cost),
                total_cost=float(position.avg_cost) * int(position.shares),
                metadata={"bootstrap": True},
            )
        )
    return lots


def apply_buy_fill_to_lots(
    lots: list[PositionLot],
    *,
    account_id: str,
    ts_code: str,
    source_id: str,
    source_type: str,
    trade_date: str,
    settle_date: str,
    available_date: str,
    shares: int,
    total_cost: float,
) -> list[PositionLot]:
    if shares <= 0:
        return list(lots)
    lot_id = "lot_" + hashlib.sha256("|".join([account_id, source_id, ts_code, str(shares)]).encode("utf-8")).hexdigest()[:20]
    if any(lot.lot_id == lot_id for lot in lots):
        return list(lots)
    unit_cost = float(total_cost) / max(int(shares), 1)
    return [
        *lots,
        PositionLot(
            lot_id=lot_id,
            account_id=account_id,
            ts_code=ts_code,
            source_id=source_id,
            source_type=source_type,
            open_date=trade_date,
            settle_date=settle_date,
            available_date=available_date,
            shares_original=int(shares),
            shares_remaining=int(shares),
            unit_cost=float(unit_cost),
            total_cost=float(total_cost),
        ),
    ]


def apply_sell_fill_to_lots(
    lots: list[PositionLot],
    *,
    ts_code: str,
    shares: int,
    proceeds: float,
    fee_tax_total: float,
    trade_date: str,
    sell_fill_id: str,
    method: str = "average",
) -> tuple[list[PositionLot], RealizedPnlRecord]:
    shares = int(max(shares, 0))
    active = [lot for lot in lots if lot.ts_code == ts_code and lot.shares_remaining > 0]
    allocations: list[dict[str, Any]] = []
    if shares <= 0 or not active:
        return list(lots), RealizedPnlRecord(trade_date, ts_code, sell_fill_id, 0, proceeds, 0.0, fee_tax_total, 0.0, method, [])
    remaining = shares
    if method == "fifo":
        ordered = sorted(active, key=lambda lot: (lot.open_date, lot.lot_id))
        cost_basis = 0.0
        updated = {lot.lot_id: lot for lot in lots}
        for lot in ordered:
            if remaining <= 0:
                break
            take = min(remaining, lot.shares_remaining)
            cost = take * lot.unit_cost
            cost_basis += cost
            allocations.append({"lot_id": lot.lot_id, "shares": take, "cost_basis": float(cost)})
            updated[lot.lot_id] = replace(lot, shares_remaining=lot.shares_remaining - take, realized_pnl=lot.realized_pnl + (proceeds * take / shares - cost))
            remaining -= take
        new_lots = [updated[lot.lot_id] for lot in lots]
    else:
        total_available = sum(lot.shares_remaining for lot in active)
        avg_cost = sum(lot.shares_remaining * lot.unit_cost for lot in active) / max(total_available, 1)
        cost_basis = min(shares, total_available) * avg_cost
        allocations.append({"lot_id": "average_cost", "shares": min(shares, total_available), "cost_basis": float(cost_basis)})
        remaining = shares
        updated = {lot.lot_id: lot for lot in lots}
        for lot in sorted(active, key=lambda item: (item.open_date, item.lot_id)):
            if remaining <= 0:
                break
            take = min(remaining, lot.shares_remaining)
            updated[lot.lot_id] = replace(lot, shares_remaining=lot.shares_remaining - take)
            remaining -= take
        new_lots = [updated[lot.lot_id] for lot in lots]
    sold = shares - max(remaining, 0)
    realized = float(proceeds) - float(cost_basis) - float(fee_tax_total)
    record = RealizedPnlRecord(
        trade_date=trade_date,
        ts_code=ts_code,
        sell_fill_id=sell_fill_id,
        shares=int(sold),
        proceeds=float(proceeds),
        allocated_cost_basis=float(cost_basis),
        fee_tax_total=float(fee_tax_total),
        realized_pnl=float(realized),
        cost_basis_method=method,
        lot_allocations=allocations,
    )
    return new_lots, record


def allocate_sell_lots(
    lots: list[PositionLot],
    *,
    ts_code: str,
    shares: int,
    proceeds: float,
    fee_tax_total: float,
    trade_date: str,
    sell_fill_id: str,
    method: str = "average",
) -> tuple[list[PositionLot], RealizedPnlRecord]:
    """Allocate a sell fill against open lots using the requested cost basis."""

    return apply_sell_fill_to_lots(
        lots,
        ts_code=ts_code,
        shares=shares,
        proceeds=proceeds,
        fee_tax_total=fee_tax_total,
        trade_date=trade_date,
        sell_fill_id=sell_fill_id,
        method=method,
    )


def adjust_lots_for_stock_distribution(lots: list[PositionLot], ts_code: str, ratio: float, action_id: str) -> list[PositionLot]:
    if ratio <= 0:
        return list(lots)
    adjusted: list[PositionLot] = []
    for lot in lots:
        if lot.ts_code != ts_code or lot.shares_remaining <= 0:
            adjusted.append(lot)
            continue
        new_remaining = int(lot.shares_remaining * (1.0 + ratio))
        new_original = int(lot.shares_original * (1.0 + ratio))
        unit_cost = lot.total_cost / max(new_original, 1)
        metadata = dict(lot.metadata)
        metadata.setdefault("corporate_actions", []).append({"action_id": action_id, "ratio": ratio})
        adjusted.append(replace(lot, shares_original=new_original, shares_remaining=new_remaining, unit_cost=float(unit_cost), metadata=metadata))
    return adjusted


def compute_position_availability(lots: list[PositionLot], trade_date: str) -> list[PositionAvailability]:
    by_code: dict[str, list[PositionLot]] = {}
    for lot in lots:
        if lot.shares_remaining <= 0:
            continue
        by_code.setdefault(lot.ts_code, []).append(lot)
    records: list[PositionAvailability] = []
    for ts_code, items in sorted(by_code.items()):
        total = sum(lot.shares_remaining for lot in items)
        available = sum(lot.shares_remaining for lot in items if lot.available_date <= trade_date)
        unsettled = total - available
        records.append(
            PositionAvailability(
                ts_code=ts_code,
                trade_date=trade_date,
                total_shares=int(total),
                available_shares=int(available),
                frozen_shares=0,
                unsettled_buy_shares=int(unsettled),
                pending_sell_shares=0,
                lot_count=len(items),
            )
        )
    return records
