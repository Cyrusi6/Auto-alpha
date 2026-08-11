"""Paper-account ledger, performance, and command workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PaperPosition:
    ts_code: str
    shares: int
    avg_cost: float
    market_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    available_shares: int = 0
    frozen_shares: int = 0
    unsettled_buy_shares: int = 0
    pending_sell_shares: int = 0
    realized_pnl: float = 0.0
    lot_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperCashLedgerEntry:
    trade_date: str
    amount: float
    balance: float
    reason: str
    ts_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperTradeLedgerEntry:
    trade_date: str
    ts_code: str
    side: str
    price: float
    shares: int
    value: float
    cost: float
    status: str
    reason: str = ""
    parent_order_id: str | None = None
    child_order_id: str | None = None
    bucket: str | None = None
    broker_order_id: str | None = None
    broker_fill_id: str | None = None
    client_order_id: str | None = None
    broker_adapter: str | None = None
    broker_batch_id: str | None = None
    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0
    slippage: float = 0.0
    market_impact: float = 0.0
    other_fee: float = 0.0
    cost_breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperCorporateActionLedgerEntry:
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
class PaperAccountSnapshot:
    trade_date: str
    equity: float
    cash: float
    positions_value: float
    daily_return: float
    n_positions: int
    exposure: float
    cash_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperAccountState:
    account_id: str
    initial_cash: float
    cash: float
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    cash_ledger: list[PaperCashLedgerEntry] = field(default_factory=list)
    trade_ledger: list[PaperTradeLedgerEntry] = field(default_factory=list)
    corporate_action_ledger: list[PaperCorporateActionLedgerEntry] = field(default_factory=list)
    settlement_ledger: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[PaperAccountSnapshot] = field(default_factory=list)
    updated_at: str | None = None
    available_cash: float | None = None
    withdrawable_cash: float | None = None
    frozen_cash: float = 0.0
    unsettled_receivable: float = 0.0
    unsettled_payable: float = 0.0
    position_lots: list[dict[str, Any]] = field(default_factory=list)
    settlement_events: list[dict[str, Any]] = field(default_factory=list)
    realized_pnl_ledger: list[dict[str, Any]] = field(default_factory=list)
    account_nav: list[dict[str, Any]] = field(default_factory=list)
    adjustment_ledger: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "initial_cash": float(self.initial_cash),
            "cash": float(self.cash),
            "positions": {key: value.to_dict() for key, value in self.positions.items()},
            "cash_ledger": [entry.to_dict() for entry in self.cash_ledger],
            "trade_ledger": [entry.to_dict() for entry in self.trade_ledger],
            "corporate_action_ledger": [entry.to_dict() for entry in self.corporate_action_ledger],
            "settlement_ledger": [dict(entry) for entry in self.settlement_ledger],
            "snapshots": [snapshot.to_dict() for snapshot in self.snapshots],
            "updated_at": self.updated_at,
            "available_cash": float(self.cash if self.available_cash is None else self.available_cash),
            "withdrawable_cash": float(self.cash if self.withdrawable_cash is None else self.withdrawable_cash),
            "frozen_cash": float(self.frozen_cash),
            "unsettled_receivable": float(self.unsettled_receivable),
            "unsettled_payable": float(self.unsettled_payable),
            "position_lots": [dict(entry) for entry in self.position_lots],
            "settlement_events": [dict(entry) for entry in self.settlement_events],
            "realized_pnl_ledger": [dict(entry) for entry in self.realized_pnl_ledger],
            "account_nav": [dict(entry) for entry in self.account_nav],
            "adjustment_ledger": [dict(entry) for entry in self.adjustment_ledger],
        }

import json
import math
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



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
        from auto_alpha.execution.settlement.engine import SettlementCalendar, build_settlement_events_from_fills, load_settlement_profile, apply_settlement_events

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
        from auto_alpha.execution.settlement.engine import load_settlement_profile, settle_pending_events

        updated = settle_pending_events(self.load_state(), as_of_date, prices=prices, profile=load_settlement_profile(profile))
        return self.save_state(updated)

    def precheck_orders(self, orders: Sequence[object], prices: dict[str, float] | None = None, profile: str = "cn_ashare_paper_default") -> dict[str, Any]:
        from auto_alpha.execution.settlement.engine import load_settlement_profile, precheck_orders_against_availability

        return precheck_orders_against_availability(self.load_state(), orders, prices=prices, profile=load_settlement_profile(profile))

    def reconcile(self, as_of_date: str) -> dict[str, Any]:
        from auto_alpha.execution.settlement.engine import reconcile_account_state

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
        from auto_alpha.execution.settlement.engine import update_cash_buckets, update_position_availability

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
        from auto_alpha.data.pit.corporate_actions.accounting import apply_corporate_actions_to_positions

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

import math



def compute_account_performance(state: PaperAccountState) -> dict[str, float]:
    snapshots = state.snapshots
    equity = snapshots[-1].equity if snapshots else state.cash
    returns = [snapshot.daily_return for snapshot in snapshots if math.isfinite(snapshot.daily_return)]
    total_return = equity / state.initial_cash - 1.0 if state.initial_cash else 0.0
    mean_return = sum(returns) / len(returns) if returns else 0.0
    variance = sum((value - mean_return) ** 2 for value in returns) / len(returns) if returns else 0.0
    volatility = math.sqrt(max(variance, 0.0))
    sharpe = mean_return / volatility * math.sqrt(252.0) if volatility > 1e-12 else 0.0
    max_drawdown = _max_drawdown([snapshot.equity for snapshot in snapshots])
    filled = [entry for entry in state.trade_ledger if entry.status in {"FILLED", "PARTIAL"}]
    rejected = [entry for entry in state.trade_ledger if entry.status == "REJECTED"]
    buys = sum(entry.value for entry in filled if entry.side == "BUY")
    sells = sum(entry.value for entry in filled if entry.side == "SELL")
    turnover = (buys + sells) / max(equity, 1.0)
    fill_rate = len(filled) / len(state.trade_ledger) if state.trade_ledger else 0.0
    unfilled_value = sum(entry.value for entry in rejected)
    realized_cost = sum(entry.cost for entry in filled)
    hit_ratio = sum(1 for snapshot in snapshots if snapshot.daily_return > 0) / len(snapshots) if snapshots else 0.0
    exposure = snapshots[-1].exposure if snapshots else 0.0
    cash_ratio = snapshots[-1].cash_ratio if snapshots else (state.cash / equity if equity else 0.0)
    return {
        "total_return": float(total_return),
        "daily_returns": float(mean_return),
        "volatility": float(volatility),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_drawdown),
        "turnover": float(turnover),
        "hit_ratio": float(hit_ratio),
        "exposure": float(exposure),
        "cash_ratio": float(cash_ratio),
        "fill_rate": float(fill_rate),
        "unfilled_value": float(unfilled_value),
        "estimated_vs_realized_cost": float(realized_cost),
    }


def _max_drawdown(equity_values: list[float]) -> float:
    peak = -float("inf")
    worst = 0.0
    for value in equity_values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return abs(float(worst))

import argparse
import json
from dataclasses import replace
from pathlib import Path



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a local paper account.")
    parser.add_argument("--account-dir", required=True)
    parser.add_argument("--account-id", default="paper_ashare")
    parser.add_argument("--pretty", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    reset = sub.add_parser("reset")
    reset.add_argument("--initial-cash", type=float, required=True)
    reset.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    show = sub.add_parser("show")
    show.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    mtm = sub.add_parser("mark-to-market")
    mtm.add_argument("--trade-date", required=True)
    mtm.add_argument("--prices-json", required=True, help="JSON object of ts_code to price.")
    mtm.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    perf = sub.add_parser("performance")
    perf.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    apply_ca = sub.add_parser("apply-corporate-actions")
    apply_ca.add_argument("--data-dir", required=True)
    apply_ca.add_argument("--corporate-action-dir")
    apply_ca.add_argument("--trade-date", required=True)
    apply_ca.add_argument("--application-date-mode", choices=("ex_date", "pay_date", "div_listdate", "record_date"), default="pay_date")
    apply_ca.add_argument("--cash-field", choices=("cash_div", "cash_div_tax"), default="cash_div")
    apply_ca.add_argument("--apply-statuses", default="实施")
    apply_ca.add_argument("--settlement-aware", action="store_true")
    apply_ca.add_argument("--settlement-dir")
    apply_ca.add_argument(
        "--settlement-profile",
        choices=("cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"),
        default="cn_ashare_paper_default",
    )
    apply_ca.add_argument("--cost-basis-method", choices=("average", "fifo"), default="average")
    apply_ca.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    show_ca = sub.add_parser("show-corporate-actions")
    show_ca.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    recon_ca = sub.add_parser("reconcile-corporate-actions")
    recon_ca.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    apply_fills = sub.add_parser("apply-fills")
    apply_fills.add_argument("--data-dir")
    apply_fills.add_argument("--fills-path", required=True)
    apply_fills.add_argument("--trade-date", required=True)
    apply_fills.add_argument("--prices-json")
    apply_fills.add_argument("--settlement-aware", action="store_true")
    apply_fills.add_argument(
        "--settlement-profile",
        choices=("cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"),
        default="cn_ashare_paper_default",
    )
    apply_fills.add_argument("--cost-basis-method", choices=("average", "fifo"), default="average")
    apply_fills.add_argument("--settlement-dir")
    apply_fills.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    settle = sub.add_parser("settle")
    settle.add_argument("--as-of-date", required=True)
    settle.add_argument("--prices-json")
    settle.add_argument(
        "--settlement-profile",
        choices=("cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"),
        default="cn_ashare_paper_default",
    )
    settle.add_argument("--settlement-dir")
    settle.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    precheck = sub.add_parser("precheck-orders")
    precheck.add_argument("--orders-path", required=True)
    precheck.add_argument("--prices-json")
    precheck.add_argument(
        "--settlement-profile",
        choices=("cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"),
        default="cn_ashare_paper_default",
    )
    precheck.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    show_settlement = sub.add_parser("show-settlement")
    show_settlement.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    show_lots = sub.add_parser("show-lots")
    show_lots.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    nav = sub.add_parser("build-nav")
    nav.add_argument("--data-dir")
    nav.add_argument("--as-of-date", required=True)
    nav.add_argument("--prices-json")
    nav.add_argument("--settlement-dir")
    nav.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    recon = sub.add_parser("reconcile-account")
    recon.add_argument("--as-of-date", required=True)
    recon.add_argument("--settlement-dir")
    recon.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    apply_adjustments = sub.add_parser("apply-adjustments")
    apply_adjustments.add_argument("--adjustments-path", required=True)
    apply_adjustments.add_argument("--approval-id", required=True)
    apply_adjustments.add_argument("--trade-date", required=True)
    apply_adjustments.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    show_adjustments = sub.add_parser("show-adjustments")
    show_adjustments.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)

    reconcile_external = sub.add_parser("reconcile-external")
    reconcile_external.add_argument("--eod-reconciliation-report-path", required=True)
    reconcile_external.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    account = LocalPaperAccount(args.account_dir, account_id=args.account_id)
    try:
        if args.command == "reset":
            payload = account.reset(args.initial_cash).to_dict()
        elif args.command == "show":
            payload = account.load_state().to_dict()
        elif args.command == "mark-to-market":
            payload = account.mark_to_market(json.loads(args.prices_json), args.trade_date).to_dict()
        elif args.command == "performance":
            payload = compute_account_performance(account.load_state())
        elif args.command == "apply-corporate-actions":
            from auto_alpha.data.pit.corporate_actions.models import CorporateActionEvent
            from auto_alpha.data.pit.corporate_actions.normalizer import normalize_corporate_action_records
            from auto_alpha.data.pit.corporate_actions.report import read_jsonl

            event_path = (args.corporate_action_dir and f"{args.corporate_action_dir}/corporate_action_events.jsonl") or None
            if event_path and Path(event_path).exists():
                events = [CorporateActionEvent(**record) for record in read_jsonl(event_path)]
            else:
                records = read_jsonl(f"{args.data_dir}/corporate_actions/records.jsonl")
                statuses = tuple(item.strip() for item in args.apply_statuses.split(",") if item.strip())
                events = normalize_corporate_action_records(records, statuses or ("实施",), cash_field=args.cash_field)
            state, applications = account.apply_corporate_actions(
                events,
                trade_date=args.trade_date,
                mode=args.application_date_mode,
            )
            settlement_paths = {}
            if args.settlement_aware and args.settlement_dir:
                from auto_alpha.execution.settlement.engine import SettlementCalendar, apply_settlement_events, build_settlement_events_from_corporate_actions, load_settlement_profile
                from auto_alpha.execution.settlement.engine import write_settlement_report

                profile = load_settlement_profile(args.settlement_profile, cost_basis_method=args.cost_basis_method)
                calendar = SettlementCalendar.from_data_dir(args.data_dir)
                settlement_events = build_settlement_events_from_corporate_actions(applications, profile=profile, calendar=calendar, account_id=state.account_id)
                state = account.save_state(apply_settlement_events(state, settlement_events, args.trade_date, profile=profile))
                settlement_paths = write_settlement_report(state, args.settlement_dir, args.trade_date, profile_name=profile.profile_name)
            payload = {
                "account_id": state.account_id,
                "cash": state.cash,
                "positions": {key: value.to_dict() for key, value in state.positions.items()},
                "applications": [application.to_dict() for application in applications],
                "applied_corporate_action_count": sum(application.status == "APPLIED" for application in applications),
                "corporate_action_ledger_path": str(account.corporate_action_ledger_path),
                "settlement_ledger_path": str(account.settlement_ledger_path),
                "settlement_paths": settlement_paths,
            }
        elif args.command == "show-corporate-actions":
            state = account.load_state()
            payload = {
                "corporate_action_ledger": [entry.to_dict() for entry in state.corporate_action_ledger],
                "settlement_ledger": state.settlement_ledger,
            }
        elif args.command == "reconcile-corporate-actions":
            state = account.load_state()
            ids = [entry.metadata.get("application_id") for entry in state.corporate_action_ledger]
            payload = {
                "ledger_entries": len(state.corporate_action_ledger),
                "duplicate_application_ids": len(ids) - len(set(ids)),
                "cash_ledger_entries": sum(entry.reason == "corporate_action_cash_dividend" for entry in state.cash_ledger),
            }
        elif args.command == "apply-fills":
            from auto_alpha.data.pit.corporate_actions.report import read_jsonl

            fills = read_jsonl(args.fills_path)
            prices = json.loads(args.prices_json) if args.prices_json else None
            if args.settlement_aware:
                if not args.data_dir:
                    raise ValueError("--data-dir is required for settlement-aware apply-fills")
                state = account.apply_fills_settlement_aware(
                    fills,
                    data_dir=args.data_dir,
                    trade_date=args.trade_date,
                    profile=args.settlement_profile,
                    prices=prices,
                    cost_basis_method=args.cost_basis_method,
                )
            else:
                state = account.apply_fills(fills, prices=prices, trade_date=args.trade_date)
            settlement_paths = {}
            if args.settlement_dir:
                from auto_alpha.execution.settlement.engine import write_settlement_report

                settlement_paths = write_settlement_report(state, args.settlement_dir, args.trade_date, profile_name=args.settlement_profile)
            payload = {"account_id": state.account_id, "cash": state.cash, "positions": len(state.positions), "settlement_paths": settlement_paths}
        elif args.command == "settle":
            prices = json.loads(args.prices_json) if args.prices_json else None
            state = account.settle(args.as_of_date, prices=prices, profile=args.settlement_profile)
            settlement_paths = {}
            if args.settlement_dir:
                from auto_alpha.execution.settlement.engine import write_settlement_report

                settlement_paths = write_settlement_report(state, args.settlement_dir, args.as_of_date, profile_name=args.settlement_profile)
            payload = {"account_id": state.account_id, "cash": state.cash, "pending_events": sum(event.get("status") == "pending" for event in state.settlement_events), "settlement_paths": settlement_paths}
        elif args.command == "precheck-orders":
            from auto_alpha.data.pit.corporate_actions.report import read_jsonl

            prices = json.loads(args.prices_json) if args.prices_json else None
            payload = account.precheck_orders(read_jsonl(args.orders_path), prices=prices, profile=args.settlement_profile)
        elif args.command == "show-settlement":
            state = account.load_state()
            payload = {
                "cash": state.cash,
                "available_cash": state.available_cash,
                "withdrawable_cash": state.withdrawable_cash,
                "frozen_cash": state.frozen_cash,
                "unsettled_receivable": state.unsettled_receivable,
                "unsettled_payable": state.unsettled_payable,
                "settlement_events": state.settlement_events,
                "position_lots": state.position_lots,
                "realized_pnl_ledger": state.realized_pnl_ledger,
                "account_nav": state.account_nav,
            }
        elif args.command == "show-lots":
            payload = {"position_lots": account.load_state().position_lots}
        elif args.command == "build-nav":
            from auto_alpha.execution.settlement.engine import build_account_nav_series
            from auto_alpha.execution.settlement.engine import write_settlement_report

            prices = json.loads(args.prices_json) if args.prices_json else _load_prices_from_data_dir(args.data_dir, args.as_of_date)
            state = account.load_state()
            nav = build_account_nav_series(state, prices_by_date={args.as_of_date: prices})
            state = account.save_state(replace(state, account_nav=[record.to_dict() for record in nav]))
            settlement_paths = write_settlement_report(state, args.settlement_dir, args.as_of_date) if args.settlement_dir else {}
            payload = {"nav_records": [record.to_dict() for record in nav], "settlement_paths": settlement_paths}
        elif args.command == "reconcile-account":
            report = account.reconcile(args.as_of_date)
            settlement_paths = {}
            if args.settlement_dir:
                from auto_alpha.execution.settlement.engine import write_settlement_report

                settlement_paths = write_settlement_report(account.load_state(), args.settlement_dir, args.as_of_date)
            payload = report | {"settlement_paths": settlement_paths}
        elif args.command == "apply-adjustments":
            from auto_alpha.data.pit.corporate_actions.report import read_jsonl

            state, applied, skipped = account.apply_adjustments(read_jsonl(args.adjustments_path), args.approval_id, args.trade_date)
            payload = {
                "account_id": state.account_id,
                "cash": state.cash,
                "positions": len(state.positions),
                "applied_count": len(applied),
                "skipped_duplicate_count": skipped,
                "adjustment_ledger_path": str(account.adjustment_ledger_path),
            }
        elif args.command == "show-adjustments":
            payload = {"adjustment_ledger": account.load_state().adjustment_ledger, "adjustment_ledger_path": str(account.adjustment_ledger_path)}
        elif args.command == "reconcile-external":
            payload = json.loads(Path(args.eod_reconciliation_report_path).read_text(encoding="utf-8"))
        else:  # pragma: no cover
            raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _load_prices_from_data_dir(data_dir: str | None, trade_date: str) -> dict[str, float]:
    if not data_dir:
        return {}
    from auto_alpha.data.pit.corporate_actions.report import read_jsonl

    path = Path(data_dir) / "daily_bars" / "records.jsonl"
    if not path.exists():
        return {}
    return {
        str(record.get("ts_code")): float(record.get("close") or 0.0)
        for record in read_jsonl(path)
        if str(record.get("trade_date")) == trade_date
    }


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "LocalPaperAccount",
    "PaperAccountSnapshot",
    "PaperAccountState",
    "PaperCashLedgerEntry",
    "PaperPosition",
    "PaperTradeLedgerEntry",
    "compute_account_performance",
]
