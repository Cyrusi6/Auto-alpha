"""Pure-Python records for the Task 055-A event-ledger simulator."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CashBuckets:
    available: float
    frozen: float = 0.0
    unsettled_receivable: float = 0.0
    unsettled_payable: float = 0.0
    withdrawable: float | None = None

    def __post_init__(self) -> None:
        self.available = float(self.available)
        self.frozen = float(self.frozen)
        self.unsettled_receivable = float(self.unsettled_receivable)
        self.unsettled_payable = float(self.unsettled_payable)
        if self.withdrawable is None:
            self.withdrawable = self.available
        self.withdrawable = float(self.withdrawable)

    @property
    def total(self) -> float:
        return float(
            self.available
            + self.frozen
            + self.unsettled_receivable
            - self.unsettled_payable
        )

    @property
    def total_cash(self) -> float:
        return self.total

    @property
    def available_cash(self) -> float:
        return self.available

    @property
    def frozen_cash(self) -> float:
        return self.frozen

    def to_dict(self) -> dict[str, float]:
        payload = asdict(self)
        payload["total"] = self.total
        return payload


@dataclass
class PositionLot:
    lot_id: str
    asset: str
    shares: int
    acquired_index: int
    available_index: int
    unit_cost: float
    source: str = "buy_fill"

    @property
    def available(self) -> bool:
        return self.available_index <= self.acquired_index

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Order:
    order_id: str
    decision_index: int
    execution_index: int
    asset: str
    side: str
    requested_shares: int
    target_shares: int
    decision_price: float
    target_weight: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    execution_index: int
    asset: str
    side: str
    requested_shares: int
    filled_shares: int
    price: float
    notional: float
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage: float
    impact: float
    total_cost: float
    status: str
    capacity_shares: int
    lagged_adv: float
    handling_fee: float = 0.0
    securities_management_fee: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def market_impact(self) -> float:
        return self.impact


@dataclass(frozen=True)
class Rejection:
    order_id: str
    execution_index: int
    asset: str
    side: str
    requested_shares: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SettlementEvent:
    event_id: str
    event_type: str
    created_index: int
    settle_index: int
    asset: str | None = None
    shares: int = 0
    cash_amount: float = 0.0
    source_id: str = ""
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CorporateActionEvent:
    action_id: str
    effective_index: int
    asset: str
    cash_dividend_per_share: float = 0.0
    share_ratio: float = 1.0
    pay_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NavRecord:
    index: int
    date: str
    open_pre: float
    open_post: float
    close: float
    prior_open_post: float | None
    open_to_open_return: float | None
    positions_open: float
    positions_close: float
    cash_total: float
    available_cash: float
    unsettled_cash: float
    daily_cost: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def nav_open_pre(self) -> float:
        return self.open_pre

    @property
    def nav_open_post(self) -> float:
        return self.open_post

    @property
    def nav_close(self) -> float:
        return self.close


@dataclass
class LedgerState:
    cash: CashBuckets
    lots: dict[str, list[PositionLot]] = field(default_factory=dict)
    frozen_shares: dict[str, int] = field(default_factory=dict)
    pending_settlements: list[SettlementEvent] = field(default_factory=list)

    def total_shares(self, asset: str) -> int:
        return sum(max(0, int(lot.shares)) for lot in self.lots.get(asset, ()))

    def available_shares(self, asset: str, index: int) -> int:
        gross = sum(
            max(0, int(lot.shares))
            for lot in self.lots.get(asset, ())
            if lot.available_index <= index
        )
        return max(0, gross - int(self.frozen_shares.get(asset, 0)))

    def unsettled_shares(self, asset: str, index: int) -> int:
        return sum(
            max(0, int(lot.shares))
            for lot in self.lots.get(asset, ())
            if lot.available_index > index
        )

    def position_snapshot(self, index: int) -> dict[str, dict[str, int]]:
        assets = sorted(set(self.lots) | set(self.frozen_shares))
        return {
            asset: {
                "total": self.total_shares(asset),
                "available": self.available_shares(asset, index),
                "frozen": int(self.frozen_shares.get(asset, 0)),
                "unsettled": self.unsettled_shares(asset, index),
            }
            for asset in assets
        }


@dataclass
class SimulationResult:
    dates: list[str]
    assets: list[str]
    orders: list[Order]
    fills: list[Fill]
    rejections: list[Rejection]
    settlements: list[SettlementEvent]
    corporate_actions: list[CorporateActionEvent]
    nav: list[NavRecord]
    final_cash: CashBuckets
    final_positions: dict[str, dict[str, int]]
    event_ledger: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dates": list(self.dates),
            "assets": list(self.assets),
            "orders": [item.to_dict() for item in self.orders],
            "fills": [item.to_dict() for item in self.fills],
            "rejections": [item.to_dict() for item in self.rejections],
            "settlements": [item.to_dict() for item in self.settlements],
            "corporate_actions": [item.to_dict() for item in self.corporate_actions],
            "nav": [item.to_dict() for item in self.nav],
            "final_cash": self.final_cash.to_dict(),
            "final_positions": self.final_positions,
            "event_ledger": list(self.event_ledger),
        }

    @property
    def nav_records(self) -> list[NavRecord]:
        return self.nav

    @property
    def ledger(self) -> list[dict[str, Any]]:
        return self.event_ledger


AccountState = LedgerState
OrderEvent = Order
FillEvent = Fill
RejectionEvent = Rejection
