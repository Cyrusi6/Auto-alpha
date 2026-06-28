"""Persistent local paper account ledger."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from artifact_schema.writer import write_json_artifact

from .models import (
    PaperAccountSnapshot,
    PaperAccountState,
    PaperCashLedgerEntry,
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
            snapshots=state.snapshots,
            updated_at=_utc_now(),
        )
        self.root_dir.mkdir(parents=True, exist_ok=True)
        write_json_artifact(self.state_path, updated.to_dict(), artifact_type="paper_account_state", producer="paper_account")
        self.export_positions(updated)
        self.export_snapshots(updated)
        self.export_trade_ledger(updated)
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
            snapshots=state.snapshots,
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
    ) -> PaperAccountState:
        return self.apply_fills(child_fills, prices=prices, trade_date=trade_date)

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
            snapshots=state.snapshots + [snapshot],
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
            snapshots=state.snapshots,
            updated_at=state.updated_at,
        )


def _state_from_payload(payload: dict[str, Any]) -> PaperAccountState:
    return PaperAccountState(
        account_id=str(payload.get("account_id") or "paper_ashare"),
        initial_cash=float(payload.get("initial_cash") or 0.0),
        cash=float(payload.get("cash") or 0.0),
        positions={key: PaperPosition(**value) for key, value in dict(payload.get("positions") or {}).items()},
        cash_ledger=[PaperCashLedgerEntry(**entry) for entry in payload.get("cash_ledger", [])],
        trade_ledger=[PaperTradeLedgerEntry(**entry) for entry in payload.get("trade_ledger", [])],
        snapshots=[PaperAccountSnapshot(**entry) for entry in payload.get("snapshots", [])],
        updated_at=payload.get("updated_at"),
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


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return path


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
