"""Persistent local paper account ledger."""

from __future__ import annotations

import json
import math
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from artifact_schema.writer import write_json_artifact

from .models import (
    PaperAccountSnapshot,
    PaperAccountState,
    PaperCashLedgerEntry,
    PaperCorporateActionLedgerEntry,
    PaperPosition,
    PaperTradeLedgerEntry,
)


class LocalPaperAccount:
    def __init__(self, root_dir: str | Path, account_id: str = "paper_ashare"):
        self.root_dir = Path(root_dir)
        self.account_id = account_id
        self.state_path = self.root_dir / "account_state.json"
        self.positions_path = self.root_dir / "positions.jsonl"
        self.cash_ledger_path = self.root_dir / "cash_ledger.jsonl"
        self.trade_ledger_path = self.root_dir / "trade_ledger.jsonl"
        self.snapshots_path = self.root_dir / "account_snapshots.jsonl"
        self.corporate_action_ledger_path = self.root_dir / "corporate_action_ledger.jsonl"
        self.settlement_ledger_path = self.root_dir / "settlement_ledger.jsonl"
        self.position_lots_path = self.root_dir / "position_lots.jsonl"
        self.settlement_events_path = self.root_dir / "settlement_events.jsonl"
        self.cash_buckets_path = self.root_dir / "cash_buckets.jsonl"
        self.position_availability_path = self.root_dir / "position_availability.jsonl"
        self.realized_pnl_path = self.root_dir / "realized_pnl.jsonl"
        self.account_nav_path = self.root_dir / "account_nav.jsonl"
        self.account_performance_report_path = self.root_dir / "account_performance_report.json"
        self.adjustment_ledger_path = self.root_dir / "adjustment_ledger.jsonl"

    def load_state(self) -> PaperAccountState:
        if not self.state_path.exists():
            return PaperAccountState(account_id=self.account_id, initial_cash=0.0, cash=0.0)
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return _state_from_payload(payload)

    def save_state(self, state: PaperAccountState) -> PaperAccountState:
        if not math.isfinite(float(state.cash)):
            raise ValueError("paper account cash must be finite")
        updated = PaperAccountState(
            account_id=state.account_id,
            initial_cash=float(state.initial_cash),
            cash=float(state.cash),
            positions=state.positions,
            cash_ledger=state.cash_ledger,
            trade_ledger=state.trade_ledger,
            corporate_action_ledger=state.corporate_action_ledger,
            settlement_ledger=state.settlement_ledger,
            snapshots=state.snapshots,
            updated_at=_utc_now(),
            available_cash=float(state.cash if state.available_cash is None else state.available_cash),
            withdrawable_cash=float(state.cash if state.withdrawable_cash is None else state.withdrawable_cash),
            frozen_cash=float(state.frozen_cash),
            unsettled_receivable=float(state.unsettled_receivable),
            unsettled_payable=float(state.unsettled_payable),
            position_lots=state.position_lots,
            settlement_events=state.settlement_events or state.settlement_ledger,
            realized_pnl_ledger=state.realized_pnl_ledger,
            account_nav=state.account_nav,
            adjustment_ledger=state.adjustment_ledger,
        )
        self.root_dir.mkdir(parents=True, exist_ok=True)
        write_json_artifact(self.state_path, updated.to_dict(), artifact_type="paper_account_state", producer="paper_account")
        self.export_positions(updated)
        self.export_snapshots(updated)
        self.export_trade_ledger(updated)
        self.export_corporate_action_ledger(updated)
        self.export_settlement_ledger(updated)
        self.export_settlement_artifacts(updated)
        self.export_adjustment_ledger(updated)
        self._export_cash_ledger(updated)
        return updated

    def reset(self, initial_cash: float) -> PaperAccountState:
        cash = float(initial_cash)
        if not math.isfinite(cash) or cash < 0:
            raise ValueError("initial_cash must be a finite non-negative number")
        state = PaperAccountState(
            account_id=self.account_id,
            initial_cash=cash,
            cash=cash,
            cash_ledger=[
                PaperCashLedgerEntry(
                    trade_date="INIT",
                    amount=cash,
                    balance=cash,
                    reason="reset",
                )
            ],
            available_cash=cash,
            withdrawable_cash=cash,
            updated_at=_utc_now(),
        )
        return self.save_state(state)

    def apply_fills(
        self,
        fills: Sequence[object],
        prices: dict[str, float] | None = None,
        trade_date: str | None = None,
    ) -> PaperAccountState:
        state = self.load_state()
        positions = dict(state.positions)
        cash = float(state.cash)
        cash_ledger = list(state.cash_ledger)
        trade_ledger = list(state.trade_ledger)
        applied_fill_keys = {_fill_key(entry.to_dict()) for entry in trade_ledger}
        for fill in fills:
            payload = _fill_payload(fill)
            fill_date = str(trade_date or payload.get("trade_date") or "")
            ts_code = str(payload.get("ts_code") or "")
            side = str(payload.get("side") or "").upper()
            status = str(payload.get("status") or "")
            price = float(payload.get("price") or 0.0)
            shares = int(payload.get("shares") or 0)
            value = float(payload.get("value") or 0.0)
            cost = float(payload.get("cost") or 0.0)
            reason = str(payload.get("reason") or "")
            fill_key = _fill_key(payload | {"trade_date": fill_date})
            if fill_key in applied_fill_keys:
                continue
            applied_fill_keys.add(fill_key)
            trade_ledger.append(
                PaperTradeLedgerEntry(
                    trade_date=fill_date,
                    ts_code=ts_code,
                    side=side,
                    price=price,
                    shares=shares,
                    value=value,
                    cost=cost,
                    status=status,
                    reason=reason,
                    parent_order_id=payload.get("parent_order_id"),
                    child_order_id=payload.get("child_order_id"),
                    bucket=payload.get("bucket"),
                    broker_order_id=payload.get("broker_order_id"),
                    broker_fill_id=payload.get("broker_fill_id"),
                    client_order_id=payload.get("client_order_id"),
                    broker_adapter=payload.get("broker_adapter"),
                    broker_batch_id=payload.get("broker_batch_id"),
                    commission=float(payload.get("commission") or 0.0),
                    stamp_duty=float(payload.get("stamp_duty") or 0.0),
                    transfer_fee=float(payload.get("transfer_fee") or 0.0),
                    slippage=float(payload.get("slippage") or 0.0),
                    market_impact=float(payload.get("market_impact") or 0.0),
                    other_fee=float(payload.get("other_fee") or 0.0),
                    cost_breakdown=dict(payload.get("cost_breakdown") or {}),
                )
            )
            if status not in {"FILLED", "PARTIAL"} or shares <= 0:
                continue
            existing = positions.get(ts_code, PaperPosition(ts_code=ts_code, shares=0, avg_cost=0.0))
            if side == "BUY":
                cash_delta = -(value + cost)
                new_shares = existing.shares + shares
                avg_cost = ((existing.avg_cost * existing.shares) + value + cost) / max(new_shares, 1)
                positions[ts_code] = PaperPosition(ts_code=ts_code, shares=new_shares, avg_cost=float(avg_cost))
            elif side == "SELL":
                if shares > existing.shares:
                    raise ValueError(f"cannot sell more shares than current position for {ts_code}")
                cash_delta = value - cost
                new_shares = existing.shares - shares
                if new_shares > 0:
                    positions[ts_code] = PaperPosition(ts_code=ts_code, shares=new_shares, avg_cost=existing.avg_cost)
                else:
                    positions.pop(ts_code, None)
            else:
                continue
            cash += cash_delta
            cash_ledger.append(
                PaperCashLedgerEntry(
                    trade_date=fill_date,
                    amount=float(cash_delta),
                    balance=float(cash),
                    reason=f"{side.lower()}_{status.lower()}",
                    ts_code=ts_code,
                )
            )
        updated = PaperAccountState(
            account_id=state.account_id,
            initial_cash=state.initial_cash,
            cash=cash,
            positions=positions,
            cash_ledger=cash_ledger,
            trade_ledger=trade_ledger,
            corporate_action_ledger=state.corporate_action_ledger,
            settlement_ledger=state.settlement_ledger,
            snapshots=state.snapshots,
            available_cash=float(cash),
            withdrawable_cash=float(cash),
            frozen_cash=state.frozen_cash,
            unsettled_receivable=state.unsettled_receivable,
            unsettled_payable=state.unsettled_payable,
            position_lots=state.position_lots,
            settlement_events=state.settlement_events,
            realized_pnl_ledger=state.realized_pnl_ledger,
            account_nav=state.account_nav,
            adjustment_ledger=state.adjustment_ledger,
            updated_at=_utc_now(),
        )
        if prices:
            updated = self._mark_positions(updated, prices)
        return self.save_state(updated)

    def apply_child_fills(
        self,
        child_fills: Sequence[object],
        prices: dict[str, float] | None = None,
        trade_date: str | None = None,
        settlement_aware: bool = False,
        data_dir: str | Path | None = None,
        profile: str = "cn_ashare_paper_default",
        cost_basis_method: str = "average",
    ) -> PaperAccountState:
        if settlement_aware:
            if data_dir is None:
                raise ValueError("settlement-aware child fills require data_dir")
            return self.apply_fills_settlement_aware(
                child_fills,
                data_dir=data_dir,
                trade_date=trade_date or "",
                profile=profile,
                prices=prices,
                cost_basis_method=cost_basis_method,
            )
        return self.apply_fills(child_fills, prices=prices, trade_date=trade_date)

    def apply_fills_settlement_aware(
        self,
        fills: Sequence[object],
        data_dir: str | Path,
        trade_date: str,
        profile: str = "cn_ashare_paper_default",
        prices: dict[str, float] | None = None,
        cost_basis_method: str = "average",
    ) -> PaperAccountState:
        from settlement_engine import SettlementCalendar, build_settlement_events_from_fills, load_settlement_profile, apply_settlement_events

        settlement_profile = load_settlement_profile(profile, cost_basis_method=cost_basis_method)
        calendar = SettlementCalendar.from_data_dir(data_dir)
        events = build_settlement_events_from_fills(
            fills,
            trade_date=trade_date,
            profile=settlement_profile,
            calendar=calendar,
            account_id=self.account_id,
        )
        state = self._append_trade_ledger_only(self.load_state(), fills, trade_date)
        updated = apply_settlement_events(state, events, trade_date, prices=prices, profile=settlement_profile)
        return self.save_state(updated)

    def _append_trade_ledger_only(
        self,
        state: PaperAccountState,
        fills: Sequence[object],
        trade_date: str,
    ) -> PaperAccountState:
        trade_ledger = list(state.trade_ledger)
        applied_fill_keys = {_fill_key(entry.to_dict()) for entry in trade_ledger}
        for fill in fills:
            payload = _fill_payload(fill)
            fill_date = str(payload.get("trade_date") or trade_date)
            fill_key = _fill_key(payload | {"trade_date": fill_date})
            if fill_key in applied_fill_keys:
                continue
            applied_fill_keys.add(fill_key)
            trade_ledger.append(
                PaperTradeLedgerEntry(
                    trade_date=fill_date,
                    ts_code=str(payload.get("ts_code") or ""),
                    side=str(payload.get("side") or "").upper(),
                    price=float(payload.get("price") or 0.0),
                    shares=int(payload.get("shares") or 0),
                    value=float(payload.get("value") or 0.0),
                    cost=float(payload.get("cost") or 0.0),
                    status=str(payload.get("status") or ""),
                    reason=str(payload.get("reason") or ""),
                    parent_order_id=payload.get("parent_order_id"),
                    child_order_id=payload.get("child_order_id"),
                    bucket=payload.get("bucket"),
                    broker_order_id=payload.get("broker_order_id"),
                    broker_fill_id=payload.get("broker_fill_id"),
                    client_order_id=payload.get("client_order_id"),
                    broker_adapter=payload.get("broker_adapter"),
                    broker_batch_id=payload.get("broker_batch_id"),
                    commission=float(payload.get("commission") or 0.0),
                    stamp_duty=float(payload.get("stamp_duty") or 0.0),
                    transfer_fee=float(payload.get("transfer_fee") or 0.0),
                    slippage=float(payload.get("slippage") or 0.0),
                    market_impact=float(payload.get("market_impact") or 0.0),
                    other_fee=float(payload.get("other_fee") or 0.0),
                    cost_breakdown=dict(payload.get("cost_breakdown") or {}),
                )
            )
        return PaperAccountState(
            account_id=state.account_id,
            initial_cash=state.initial_cash,
            cash=state.cash,
            positions=state.positions,
            cash_ledger=state.cash_ledger,
            trade_ledger=trade_ledger,
            corporate_action_ledger=state.corporate_action_ledger,
            settlement_ledger=state.settlement_ledger,
            snapshots=state.snapshots,
            updated_at=state.updated_at,
            available_cash=state.available_cash,
            withdrawable_cash=state.withdrawable_cash,
            frozen_cash=state.frozen_cash,
            unsettled_receivable=state.unsettled_receivable,
            unsettled_payable=state.unsettled_payable,
            position_lots=state.position_lots,
            settlement_events=state.settlement_events,
            realized_pnl_ledger=state.realized_pnl_ledger,
            account_nav=state.account_nav,
            adjustment_ledger=state.adjustment_ledger,
        )

    def apply_adjustments(self, adjustments: Sequence[dict[str, Any]], approval_id: str, trade_date: str) -> tuple[PaperAccountState, list[dict[str, Any]], int]:
        state = self.load_state()
        existing_ids = {str(entry.get("adjustment_id") or "") for entry in state.adjustment_ledger}
        cash = float(state.cash)
        available_cash = float(state.available_cash if state.available_cash is not None else state.cash)
        withdrawable_cash = float(state.withdrawable_cash if state.withdrawable_cash is not None else state.cash)
        positions = dict(state.positions)
        settlement_events = [dict(entry) for entry in (state.settlement_events or state.settlement_ledger)]
        position_lots = [dict(entry) for entry in state.position_lots]
        cash_ledger = list(state.cash_ledger)
        ledger = list(state.adjustment_ledger)
        applied: list[dict[str, Any]] = []
        skipped = 0
        for adjustment in adjustments:
            adjustment_id = str(adjustment.get("adjustment_id") or "")
            if not adjustment_id or adjustment_id in existing_ids:
                skipped += 1
                continue
            existing_ids.add(adjustment_id)
            adjustment_type = str(adjustment.get("adjustment_type") or "")
            ts_code = adjustment.get("ts_code")
            cash_amount = float(adjustment.get("cash_amount", 0.0) or 0.0)
            share_delta = int(adjustment.get("share_delta", 0) or 0)
            if adjustment_type == "cash_manual_adjustment" and abs(cash_amount) > 1e-12:
                cash += cash_amount
                available_cash += cash_amount
                withdrawable_cash += cash_amount
                cash_ledger.append(
                    PaperCashLedgerEntry(
                        trade_date=trade_date,
                        amount=float(cash_amount),
                        balance=float(cash),
                        reason="manual_reconciliation_adjustment",
                        ts_code=None,
                    )
                )
                settlement_events.append(_manual_adjustment_event(state.account_id, adjustment_id, trade_date, None, 0, cash_amount, adjustment))
            elif adjustment_type == "position_manual_adjustment" and ts_code and share_delta:
                existing = positions.get(str(ts_code), PaperPosition(ts_code=str(ts_code), shares=0, avg_cost=0.0))
                new_shares = max(int(existing.shares) + share_delta, 0)
                positions[str(ts_code)] = PaperPosition(
                    ts_code=str(ts_code),
                    shares=new_shares,
                    avg_cost=float(existing.avg_cost),
                    market_price=float(existing.market_price),
                    market_value=float(existing.market_price) * new_shares,
                    unrealized_pnl=float(existing.unrealized_pnl),
                    available_shares=max(int(existing.available_shares) + share_delta, 0),
                    frozen_shares=existing.frozen_shares,
                    unsettled_buy_shares=existing.unsettled_buy_shares,
                    pending_sell_shares=existing.pending_sell_shares,
                    realized_pnl=existing.realized_pnl,
                    lot_count=existing.lot_count,
                )
                settlement_events.append(_manual_adjustment_event(state.account_id, adjustment_id, trade_date, str(ts_code), share_delta, 0.0, adjustment))
                if share_delta > 0:
                    position_lots.append(
                        {
                            "lot_id": _stable_id("lot_manual", state.account_id, adjustment_id, str(ts_code)),
                            "account_id": state.account_id,
                            "ts_code": str(ts_code),
                            "source_id": adjustment_id,
                            "source_type": "manual_reconciliation_adjustment",
                            "open_date": trade_date,
                            "settle_date": trade_date,
                            "available_date": trade_date,
                            "shares_original": int(share_delta),
                            "shares_remaining": int(share_delta),
                            "unit_cost": float(existing.avg_cost),
                            "total_cost": float(existing.avg_cost) * int(share_delta),
                            "realized_pnl": 0.0,
                            "status": "open",
                            "metadata": {"approval_id": approval_id, "adjustment_id": adjustment_id},
                        }
                    )
            entry = {
                **dict(adjustment),
                "approval_id": approval_id,
                "trade_date": trade_date,
                "applied_at": _utc_now(),
                "status": "APPLIED",
            }
            ledger.append(entry)
            applied.append(entry)
        account_nav = list(state.account_nav)
        if applied:
            positions_value = sum(float(position.market_value) for position in positions.values())
            account_nav.append(
                {
                    "trade_date": trade_date,
                    "equity": float(cash + positions_value),
                    "cash": float(cash),
                    "positions_value": float(positions_value),
                    "unsettled_cash": float(state.unsettled_receivable - state.unsettled_payable),
                    "frozen_cash": float(state.frozen_cash),
                    "realized_pnl": sum(float(record.get("realized_pnl", 0.0) or 0.0) for record in state.realized_pnl_ledger),
                    "unrealized_pnl": sum(float(position.unrealized_pnl) for position in positions.values()),
                    "fees": 0.0,
                    "taxes": 0.0,
                    "corporate_action_cash": 0.0,
                    "daily_return": 0.0,
                    "source": "manual_reconciliation_adjustment",
                    "approval_id": approval_id,
                }
            )
        updated = PaperAccountState(
            account_id=state.account_id,
            initial_cash=state.initial_cash,
            cash=cash,
            positions=positions,
            cash_ledger=cash_ledger,
            trade_ledger=state.trade_ledger,
            corporate_action_ledger=state.corporate_action_ledger,
            settlement_ledger=settlement_events,
            snapshots=state.snapshots,
            available_cash=available_cash,
            withdrawable_cash=withdrawable_cash,
            frozen_cash=state.frozen_cash,
            unsettled_receivable=state.unsettled_receivable,
            unsettled_payable=state.unsettled_payable,
            position_lots=position_lots,
            settlement_events=settlement_events,
            realized_pnl_ledger=state.realized_pnl_ledger,
            account_nav=account_nav,
            adjustment_ledger=ledger,
            updated_at=_utc_now(),
        )
        return self.save_state(updated), applied, skipped

    def settle(
        self,
        as_of_date: str,
        prices: dict[str, float] | None = None,
        profile: str = "cn_ashare_paper_default",
    ) -> PaperAccountState:
        from settlement_engine import load_settlement_profile, settle_pending_events

        updated = settle_pending_events(self.load_state(), as_of_date, prices=prices, profile=load_settlement_profile(profile))
        return self.save_state(updated)

    def precheck_orders(self, orders: Sequence[object], prices: dict[str, float] | None = None, profile: str = "cn_ashare_paper_default") -> dict[str, Any]:
        from settlement_engine import load_settlement_profile, precheck_orders_against_availability

        return precheck_orders_against_availability(self.load_state(), orders, prices=prices, profile=load_settlement_profile(profile))

    def reconcile(self, as_of_date: str) -> dict[str, Any]:
        from settlement_engine.reconciliation import reconcile_account_state

        return reconcile_account_state(self.load_state(), as_of_date=as_of_date).to_dict()

    def mark_to_market(self, prices: dict[str, float], trade_date: str) -> PaperAccountState:
        state = self._mark_positions(self.load_state(), prices)
        positions_value = sum(position.market_value for position in state.positions.values())
        equity = float(state.cash + positions_value)
        previous_equity = state.snapshots[-1].equity if state.snapshots else state.initial_cash
        daily_return = (equity / previous_equity - 1.0) if previous_equity else 0.0
        exposure = positions_value / equity if equity else 0.0
        snapshot = PaperAccountSnapshot(
            trade_date=trade_date,
            equity=equity,
            cash=float(state.cash),
            positions_value=float(positions_value),
            daily_return=float(daily_return),
            n_positions=sum(1 for position in state.positions.values() if position.shares > 0),
            exposure=float(exposure),
            cash_ratio=float(state.cash / equity) if equity else 0.0,
        )
        updated = PaperAccountState(
            account_id=state.account_id,
            initial_cash=state.initial_cash,
            cash=state.cash,
            positions=state.positions,
            cash_ledger=state.cash_ledger,
            trade_ledger=state.trade_ledger,
            corporate_action_ledger=state.corporate_action_ledger,
            settlement_ledger=state.settlement_ledger,
            snapshots=state.snapshots + [snapshot],
            available_cash=state.available_cash,
            withdrawable_cash=state.withdrawable_cash,
            frozen_cash=state.frozen_cash,
            unsettled_receivable=state.unsettled_receivable,
            unsettled_payable=state.unsettled_payable,
            position_lots=state.position_lots,
            settlement_events=state.settlement_events,
            realized_pnl_ledger=state.realized_pnl_ledger,
            account_nav=state.account_nav,
            adjustment_ledger=state.adjustment_ledger,
            updated_at=_utc_now(),
        )
        return self.save_state(updated)

    def export_positions(self, state: PaperAccountState | None = None) -> Path:
        state = state or self.load_state()
        return _write_jsonl(self.positions_path, [position.to_dict() for position in state.positions.values()])

    def export_snapshots(self, state: PaperAccountState | None = None) -> Path:
        state = state or self.load_state()
        return _write_jsonl(self.snapshots_path, [snapshot.to_dict() for snapshot in state.snapshots])

    def export_trade_ledger(self, state: PaperAccountState | None = None) -> Path:
        state = state or self.load_state()
        return _write_jsonl(self.trade_ledger_path, [entry.to_dict() for entry in state.trade_ledger])

    def export_corporate_action_ledger(self, state: PaperAccountState | None = None) -> Path:
        state = state or self.load_state()
        return _write_jsonl(self.corporate_action_ledger_path, [entry.to_dict() for entry in state.corporate_action_ledger])

    def export_settlement_ledger(self, state: PaperAccountState | None = None) -> Path:
        state = state or self.load_state()
        return _write_jsonl(self.settlement_ledger_path, [dict(entry) for entry in state.settlement_ledger])

    def export_settlement_artifacts(self, state: PaperAccountState | None = None) -> None:
        state = state or self.load_state()
        _write_jsonl(self.position_lots_path, [dict(entry) for entry in state.position_lots])
        _write_jsonl(self.settlement_events_path, [dict(entry) for entry in (state.settlement_events or state.settlement_ledger)])
        _write_jsonl(self.realized_pnl_path, [dict(entry) for entry in state.realized_pnl_ledger])
        _write_jsonl(self.account_nav_path, [dict(entry) for entry in state.account_nav])
        from settlement_engine import update_cash_buckets, update_position_availability

        date = _latest_date(state)
        _write_jsonl(self.cash_buckets_path, [update_cash_buckets(state, date).to_dict()])
        _write_jsonl(self.position_availability_path, [entry.to_dict() for entry in update_position_availability(state, date)])

    def export_adjustment_ledger(self, state: PaperAccountState | None = None) -> Path:
        state = state or self.load_state()
        return _write_jsonl(self.adjustment_ledger_path, [dict(entry) for entry in state.adjustment_ledger])

    def apply_corporate_actions(
        self,
        events: Sequence[object],
        trade_date: str,
        prices: dict[str, float] | None = None,
        mode: str = "pay_date",
    ) -> tuple[PaperAccountState, list[object]]:
        from corporate_actions.accounting import apply_corporate_actions_to_positions

        updated, applications = apply_corporate_actions_to_positions(
            self.load_state(),
            events,
            trade_date=trade_date,
            prices=prices,
            config={"application_date_mode": mode},
        )
        return self.save_state(updated), applications

    def _export_cash_ledger(self, state: PaperAccountState) -> Path:
        return _write_jsonl(self.cash_ledger_path, [entry.to_dict() for entry in state.cash_ledger])

    def _mark_positions(self, state: PaperAccountState, prices: dict[str, float]) -> PaperAccountState:
        positions: dict[str, PaperPosition] = {}
        for ts_code, position in state.positions.items():
            price = float(prices.get(ts_code, position.market_price or position.avg_cost))
            market_value = position.shares * price
            positions[ts_code] = PaperPosition(
                ts_code=ts_code,
                shares=position.shares,
                avg_cost=position.avg_cost,
                market_price=price,
                market_value=float(market_value),
                unrealized_pnl=float(market_value - position.avg_cost * position.shares),
            )
        return PaperAccountState(
            account_id=state.account_id,
            initial_cash=state.initial_cash,
            cash=state.cash,
            positions=positions,
            cash_ledger=state.cash_ledger,
            trade_ledger=state.trade_ledger,
            corporate_action_ledger=state.corporate_action_ledger,
            settlement_ledger=state.settlement_ledger,
            snapshots=state.snapshots,
            available_cash=state.available_cash,
            withdrawable_cash=state.withdrawable_cash,
            frozen_cash=state.frozen_cash,
            unsettled_receivable=state.unsettled_receivable,
            unsettled_payable=state.unsettled_payable,
            position_lots=state.position_lots,
            settlement_events=state.settlement_events,
            realized_pnl_ledger=state.realized_pnl_ledger,
            account_nav=state.account_nav,
            adjustment_ledger=state.adjustment_ledger,
            updated_at=state.updated_at,
        )


def _state_from_payload(payload: dict[str, Any]) -> PaperAccountState:
    cash = float(payload.get("cash") or 0.0)
    return PaperAccountState(
        account_id=str(payload.get("account_id") or "paper_ashare"),
        initial_cash=float(payload.get("initial_cash") or 0.0),
        cash=cash,
        positions={key: PaperPosition(**value) for key, value in dict(payload.get("positions") or {}).items()},
        cash_ledger=[PaperCashLedgerEntry(**entry) for entry in payload.get("cash_ledger", [])],
        trade_ledger=[PaperTradeLedgerEntry(**entry) for entry in payload.get("trade_ledger", [])],
        corporate_action_ledger=[PaperCorporateActionLedgerEntry(**entry) for entry in payload.get("corporate_action_ledger", [])],
        settlement_ledger=[dict(entry) for entry in payload.get("settlement_ledger", [])],
        snapshots=[PaperAccountSnapshot(**entry) for entry in payload.get("snapshots", [])],
        updated_at=payload.get("updated_at"),
        available_cash=float(payload.get("available_cash", cash) if payload.get("available_cash", cash) is not None else cash),
        withdrawable_cash=float(payload.get("withdrawable_cash", cash) if payload.get("withdrawable_cash", cash) is not None else cash),
        frozen_cash=float(payload.get("frozen_cash") or 0.0),
        unsettled_receivable=float(payload.get("unsettled_receivable") or 0.0),
        unsettled_payable=float(payload.get("unsettled_payable") or 0.0),
        position_lots=[dict(entry) for entry in payload.get("position_lots", [])],
        settlement_events=[dict(entry) for entry in payload.get("settlement_events", payload.get("settlement_ledger", []))],
        realized_pnl_ledger=[dict(entry) for entry in payload.get("realized_pnl_ledger", [])],
        account_nav=[dict(entry) for entry in payload.get("account_nav", [])],
        adjustment_ledger=[dict(entry) for entry in payload.get("adjustment_ledger", [])],
    )


def _fill_payload(fill: object) -> dict[str, Any]:
    if hasattr(fill, "__dataclass_fields__"):
        return {field: getattr(fill, field) for field in fill.__dataclass_fields__}
    if isinstance(fill, dict):
        return dict(fill)
    raise TypeError(f"unsupported fill record: {type(fill)!r}")


def _fill_key(payload: dict[str, Any]) -> str:
    broker_fill_id = payload.get("broker_fill_id")
    if broker_fill_id:
        return f"broker:{broker_fill_id}"
    return "fallback:" + "|".join(
        [
            str(payload.get("trade_date") or ""),
            str(payload.get("child_order_id") or ""),
            str(payload.get("ts_code") or ""),
            str(payload.get("side") or ""),
            str(payload.get("shares") or ""),
            str(payload.get("value") or ""),
            str(payload.get("status") or ""),
        ]
    )


def _manual_adjustment_event(account_id: str, adjustment_id: str, trade_date: str, ts_code: str | None, shares: int, cash_amount: float, adjustment: dict[str, Any]) -> dict[str, Any]:
    event_id = _stable_id("se_manual", account_id, adjustment_id)
    return {
        "settlement_event_id": event_id,
        "account_id": account_id,
        "source_type": "manual_reconciliation_adjustment",
        "source_id": adjustment_id,
        "trade_date": trade_date,
        "settle_date": trade_date,
        "available_date": trade_date,
        "withdrawable_date": trade_date,
        "ts_code": ts_code,
        "side": None,
        "event_type": "manual_adjustment",
        "shares": int(shares),
        "cash_amount": float(cash_amount),
        "fee_tax": {},
        "status": "settled",
        "reason": str(adjustment.get("reason") or "manual_reconciliation_adjustment"),
        "metadata": dict(adjustment),
    }


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return path


def _latest_date(state: PaperAccountState) -> str:
    if state.snapshots:
        return state.snapshots[-1].trade_date
    for ledger in (state.trade_ledger, state.cash_ledger):
        if ledger:
            return str(getattr(ledger[-1], "trade_date", "INIT") or "INIT")
    return "INIT"


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
