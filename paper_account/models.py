"""Paper account ledger dataclasses."""

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
    snapshots: list[PaperAccountSnapshot] = field(default_factory=list)
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "initial_cash": float(self.initial_cash),
            "cash": float(self.cash),
            "positions": {key: value.to_dict() for key, value in self.positions.items()},
            "cash_ledger": [entry.to_dict() for entry in self.cash_ledger],
            "trade_ledger": [entry.to_dict() for entry in self.trade_ledger],
            "snapshots": [snapshot.to_dict() for snapshot in self.snapshots],
            "updated_at": self.updated_at,
        }
