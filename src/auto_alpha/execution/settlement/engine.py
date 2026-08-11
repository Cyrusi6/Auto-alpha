"""Settlement calendar, lots, fees, events, reconciliation, performance, and reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class SettlementStatus:
    pending = "pending"
    settled = "settled"
    skipped = "skipped"
    cancelled = "cancelled"
    failed = "failed"


class SettlementEventType:
    trade_buy_cash = "trade_buy_cash"
    trade_buy_shares = "trade_buy_shares"
    trade_sell_cash = "trade_sell_cash"
    trade_sell_shares = "trade_sell_shares"
    fee_tax = "fee_tax"
    cash_dividend = "cash_dividend"
    stock_distribution = "stock_distribution"
    corporate_action_cash = "corporate_action_cash"
    corporate_action_shares = "corporate_action_shares"
    mark_to_market = "mark_to_market"
    manual_adjustment = "manual_adjustment"


@dataclass(frozen=True)
class SettlementProfile:
    profile_name: str = "cn_ashare_paper_default"
    buy_cash_settlement_lag_days: int = 0
    sell_cash_usable_lag_days: int = 1
    sell_cash_withdrawable_lag_days: int = 1
    buy_share_available_lag_days: int = 1
    sell_share_delivery_lag_days: int = 0
    corporate_cash_lag_mode: str = "pay_date"
    corporate_share_lag_mode: str = "ex_date"
    allow_same_day_sell_proceeds_for_buy: bool = False
    allow_unsettled_cash_for_buy: bool = False
    allow_unsettled_shares_for_sell: bool = False
    cost_basis_method: str = "average"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeeTaxBreakdown:
    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0
    slippage: float = 0.0
    market_impact: float = 0.0
    other_fee: float = 0.0
    total: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {key: float(value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class SettlementEvent:
    settlement_event_id: str
    account_id: str
    source_type: str
    source_id: str
    trade_date: str
    settle_date: str
    available_date: str
    withdrawable_date: str
    ts_code: str | None
    side: str | None
    event_type: str
    shares: int = 0
    cash_amount: float = 0.0
    fee_tax: dict[str, float] = field(default_factory=dict)
    status: str = SettlementStatus.pending
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PositionLot:
    lot_id: str
    account_id: str
    ts_code: str
    source_id: str
    source_type: str
    open_date: str
    settle_date: str
    available_date: str
    shares_original: int
    shares_remaining: int
    unit_cost: float
    total_cost: float
    realized_pnl: float = 0.0
    status: str = "open"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PositionAvailability:
    ts_code: str
    trade_date: str
    total_shares: int
    available_shares: int
    frozen_shares: int
    unsettled_buy_shares: int
    pending_sell_shares: int
    lot_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CashBalanceBuckets:
    trade_date: str
    total_cash: float
    available_cash: float
    withdrawable_cash: float
    frozen_cash: float = 0.0
    unsettled_receivable: float = 0.0
    unsettled_payable: float = 0.0
    reserved_buy_cash: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RealizedPnlRecord:
    trade_date: str
    ts_code: str
    sell_fill_id: str
    shares: int
    proceeds: float
    allocated_cost_basis: float
    fee_tax_total: float
    realized_pnl: float
    cost_basis_method: str
    lot_allocations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AccountNavRecord:
    trade_date: str
    equity: float
    cash: float
    positions_value: float
    unsettled_cash: float
    frozen_cash: float
    realized_pnl: float
    unrealized_pnl: float
    fees: float
    taxes: float
    corporate_action_cash: float
    daily_return: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SettlementBatchResult:
    account_id: str
    as_of_date: str
    profile: dict[str, Any]
    events: list[SettlementEvent] = field(default_factory=list)
    cash_buckets: CashBalanceBuckets | None = None
    position_availability: list[PositionAvailability] = field(default_factory=list)
    realized_pnl: list[RealizedPnlRecord] = field(default_factory=list)
    nav_records: list[AccountNavRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "as_of_date": self.as_of_date,
            "profile": self.profile,
            "events": [event.to_dict() for event in self.events],
            "cash_buckets": self.cash_buckets.to_dict() if self.cash_buckets else None,
            "position_availability": [record.to_dict() for record in self.position_availability],
            "realized_pnl": [record.to_dict() for record in self.realized_pnl],
            "nav_records": [record.to_dict() for record in self.nav_records],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class AccountReconciliationIssue:
    severity: str
    code: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AccountReconciliationReport:
    account_id: str
    as_of_date: str
    broker_fill_count: int = 0
    trade_ledger_count: int = 0
    settlement_event_count: int = 0
    pending_event_count: int = 0
    failed_event_count: int = 0
    unmatched_broker_fills: int = 0
    unmatched_trade_ledger_entries: int = 0
    unmatched_settlement_events: int = 0
    cash_difference: float = 0.0
    position_share_difference: int = 0
    lot_share_difference: int = 0
    nav_difference: float = 0.0
    realized_pnl_difference: float = 0.0
    duplicate_event_count: int = 0
    idempotent_replay_count: int = 0
    issues: list[AccountReconciliationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        issue_payloads = [issue.to_dict() for issue in self.issues]
        return {
            **asdict(self),
            "issues": issue_payloads,
            "error_count": sum(1 for issue in self.issues if issue.severity in {"error", "blocker"}),
            "warning_count": sum(1 for issue in self.issues if issue.severity == "warning"),
        }


@dataclass(frozen=True)
class SettlementReport:
    account_id: str
    as_of_date: str
    settlement_aware: bool
    settlement_profile: str
    pending_settlement_event_count: int
    failed_settlement_event_count: int
    cash_buckets: dict[str, Any]
    position_count: int
    position_lot_count: int
    realized_pnl: float
    unrealized_pnl: float
    nav_difference: float
    fee_tax_total: float
    reconciliation_error_count: int
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

import json
from pathlib import Path
from typing import Iterable



class SettlementCalendar:
    def __init__(self, trade_dates: Iterable[str], warnings: list[str] | None = None):
        self.trade_dates = sorted({str(date) for date in trade_dates if date})
        self.warnings = warnings or []

    @classmethod
    def from_data_dir(cls, data_dir: str | Path) -> "SettlementCalendar":
        path = Path(data_dir) / "trade_calendar" / "records.jsonl"
        warnings: list[str] = []
        dates: list[str] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if bool(record.get("is_open", True)):
                    dates.append(str(record.get("trade_date") or ""))
        else:
            warnings.append("trade_calendar_missing_fallback_to_observed_dates")
            for dataset in ("daily_bars", "daily_basic", "daily_limits", "adjustment_factors"):
                dataset_path = Path(data_dir) / dataset / "records.jsonl"
                if not dataset_path.exists():
                    continue
                for line in dataset_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        dates.append(str(json.loads(line).get("trade_date") or ""))
        return cls(dates, warnings=warnings)

    def next_trade_date(self, date: str, offset: int = 1) -> str:
        if not self.trade_dates:
            return str(date)
        date = str(date)
        if date in self.trade_dates:
            idx = self.trade_dates.index(date)
        else:
            idx = 0
            for i, trade_date in enumerate(self.trade_dates):
                if trade_date >= date:
                    idx = i
                    break
            else:
                idx = len(self.trade_dates) - 1
        target = max(0, min(idx + int(offset), len(self.trade_dates) - 1))
        return self.trade_dates[target]


def load_settlement_profile(name: str = "cn_ashare_paper_default", **overrides) -> SettlementProfile:
    name = name or "cn_ashare_paper_default"
    if name == "immediate_legacy":
        profile = SettlementProfile(
            profile_name=name,
            buy_cash_settlement_lag_days=0,
            sell_cash_usable_lag_days=0,
            sell_cash_withdrawable_lag_days=0,
            buy_share_available_lag_days=0,
            sell_share_delivery_lag_days=0,
            allow_same_day_sell_proceeds_for_buy=True,
            allow_unsettled_cash_for_buy=True,
            allow_unsettled_shares_for_sell=True,
        )
    elif name == "conservative_t_plus_one_cash":
        profile = SettlementProfile(
            profile_name=name,
            buy_cash_settlement_lag_days=0,
            sell_cash_usable_lag_days=1,
            sell_cash_withdrawable_lag_days=2,
            buy_share_available_lag_days=1,
            sell_share_delivery_lag_days=0,
        )
    else:
        profile = SettlementProfile(profile_name="cn_ashare_paper_default")
    payload = profile.to_dict()
    payload.update({key: value for key, value in overrides.items() if value is not None})
    return SettlementProfile(**payload)

from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact
from auto_alpha.portfolio.simulator.backtest import AShareCostModel



def estimate_fee_tax(side: str, value: float, cost_model: AShareCostModel | None = None) -> FeeTaxBreakdown:
    breakdown = (cost_model or AShareCostModel()).estimate(str(side).upper(), float(value or 0.0))
    return FeeTaxBreakdown(
        commission=float(breakdown.commission),
        stamp_duty=float(breakdown.stamp_duty),
        transfer_fee=float(breakdown.transfer_fee),
        slippage=float(breakdown.slippage),
        market_impact=float(breakdown.market_impact),
        other_fee=0.0,
        total=float(breakdown.total),
    )


def normalize_fee_tax_from_fill(fill: object, cost_model: AShareCostModel | None = None) -> tuple[FeeTaxBreakdown, list[str]]:
    payload = _engine_fee_tax_payload(fill)
    warnings: list[str] = []
    fields = ["commission", "stamp_duty", "transfer_fee", "slippage", "market_impact", "other_fee"]
    if any(payload.get(field) not in {None, ""} for field in fields):
        values = {field: float(payload.get(field) or 0.0) for field in fields}
        total = float(payload.get("cost") or payload.get("total") or sum(values.values()))
        if abs(total - sum(values.values())) > 1e-8 and not values["other_fee"]:
            values["other_fee"] += total - sum(values.values())
        return FeeTaxBreakdown(**values, total=float(total)), warnings
    value = float(payload.get("value") or 0.0)
    side = str(payload.get("side") or "")
    if value > 0 and side:
        return estimate_fee_tax(side, value, cost_model=cost_model), warnings
    total = float(payload.get("cost") or 0.0)
    if total:
        warnings.append("legacy_cost_only")
    return FeeTaxBreakdown(other_fee=total, total=total), warnings


def write_fee_tax_report(fills: list[object], path: str | Path, cost_model: AShareCostModel | None = None) -> Path:
    summary = {
        "commission": 0.0,
        "stamp_duty": 0.0,
        "transfer_fee": 0.0,
        "slippage": 0.0,
        "market_impact": 0.0,
        "other_fee": 0.0,
        "total": 0.0,
        "legacy_cost_only_count": 0,
        "fill_count": len(fills),
    }
    details = []
    for fill in fills:
        payload = _engine_fee_tax_payload(fill)
        breakdown, warnings = normalize_fee_tax_from_fill(payload, cost_model=cost_model)
        values = breakdown.to_dict()
        for key in ("commission", "stamp_duty", "transfer_fee", "slippage", "market_impact", "other_fee", "total"):
            summary[key] += float(values.get(key, 0.0) or 0.0)
        if "legacy_cost_only" in warnings:
            summary["legacy_cost_only_count"] += 1
        details.append(
            {
                "trade_date": payload.get("trade_date"),
                "ts_code": payload.get("ts_code"),
                "side": payload.get("side"),
                "status": payload.get("status"),
                "broker_fill_id": payload.get("broker_fill_id"),
                "child_order_id": payload.get("child_order_id"),
                "fee_tax": values,
                "warnings": warnings,
            }
        )
    summary["fee_tax_total"] = summary["total"]
    summary["total_fee_tax"] = summary["total"]
    payload = {"summary": summary, "details": details}
    target = Path(path)
    if target.suffix.lower() != ".json":
        target = target / "fee_tax_report.json"
    write_json_artifact(target, payload, "fee_tax_report", "settlement_engine")
    return target


def _engine_fee_tax_payload(fill: object) -> dict[str, Any]:
    if hasattr(fill, "to_dict"):
        return dict(fill.to_dict())
    if hasattr(fill, "__dataclass_fields__"):
        return {field: getattr(fill, field) for field in fill.__dataclass_fields__}
    return dict(fill)

import hashlib
from dataclasses import replace
from typing import Any



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

import hashlib
from dataclasses import replace
from typing import Any, Sequence



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
        payload = _engine_engine_payload(fill)
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
        payload = _engine_engine_payload(raw)
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
    from auto_alpha.execution.trading.paper import PaperAccountState
    from auto_alpha.execution.trading.paper import PaperCashLedgerEntry
    from auto_alpha.execution.trading.paper import PaperPosition

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
        payload = _engine_engine_payload(order)
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
    from auto_alpha.execution.trading.paper import PaperPosition

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


def _engine_engine_payload(record: object) -> dict[str, Any]:
    if hasattr(record, "to_dict"):
        return dict(record.to_dict())
    if hasattr(record, "__dataclass_fields__"):
        return {field: getattr(record, field) for field in record.__dataclass_fields__}
    return dict(record)


def _source_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256("|".join(str(payload.get(key) or "") for key in sorted(payload)).encode("utf-8")).hexdigest()[:20]

from collections import Counter
from typing import Any, Sequence



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

def build_account_nav_series(account_state, prices_by_date: dict[str, dict[str, float]] | None = None, settlement_events=None, lots=None) -> list[AccountNavRecord]:
    prices_by_date = prices_by_date or {}
    updated_at = str(getattr(account_state, "updated_at", "") or "")
    dates = sorted(prices_by_date) or [updated_at[:10].replace("-", "") or "UNKNOWN"]
    records: list[AccountNavRecord] = []
    previous_equity = float(account_state.initial_cash or account_state.cash or 0.0)
    realized = sum(float(record.get("realized_pnl", 0.0)) for record in getattr(account_state, "realized_pnl_ledger", []) or [])
    fees = sum(float(entry.cost) for entry in getattr(account_state, "trade_ledger", []) or [])
    taxes = sum(float(getattr(entry, "stamp_duty", 0.0)) for entry in getattr(account_state, "trade_ledger", []) or [])
    corporate_cash = sum(float(entry.cash_amount) for entry in getattr(account_state, "corporate_action_ledger", []) or [])
    for date in dates:
        prices = prices_by_date.get(date, {})
        positions_value = 0.0
        unrealized = 0.0
        for ts_code, position in account_state.positions.items():
            price = float(prices.get(ts_code, position.market_price or position.avg_cost))
            value = position.shares * price
            positions_value += value
            unrealized += value - position.avg_cost * position.shares
        unsettled_cash = float(getattr(account_state, "unsettled_receivable", 0.0) or 0.0) - float(getattr(account_state, "unsettled_payable", 0.0) or 0.0)
        equity = float(account_state.cash) + positions_value + unsettled_cash
        daily_return = equity / previous_equity - 1.0 if previous_equity else 0.0
        records.append(
            AccountNavRecord(
                trade_date=date,
                equity=float(equity),
                cash=float(account_state.cash),
                positions_value=float(positions_value),
                unsettled_cash=float(unsettled_cash),
                frozen_cash=float(getattr(account_state, "frozen_cash", 0.0) or 0.0),
                realized_pnl=float(realized),
                unrealized_pnl=float(unrealized),
                fees=float(fees),
                taxes=float(taxes),
                corporate_action_cash=float(corporate_cash),
                daily_return=float(daily_return),
            )
        )
        previous_equity = equity
    return records


def compute_account_performance(nav_records: list[AccountNavRecord] | list[dict]) -> dict[str, float]:
    payloads = [record.to_dict() if hasattr(record, "to_dict") else dict(record) for record in nav_records]
    if not payloads:
        return {
            "total_return": 0.0,
            "daily_return": 0.0,
            "max_drawdown": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_fees": 0.0,
            "total_stamp_duty": 0.0,
            "total_transfer_fee": 0.0,
            "total_slippage": 0.0,
            "corporate_action_cash": 0.0,
            "turnover": 0.0,
            "cash_drag": 0.0,
            "unsettled_cash_ratio": 0.0,
            "frozen_cash_ratio": 0.0,
        }
    first = float(payloads[0].get("equity", 0.0) or 0.0)
    last = float(payloads[-1].get("equity", 0.0) or 0.0)
    high = first
    max_dd = 0.0
    for item in payloads:
        equity = float(item.get("equity", 0.0) or 0.0)
        high = max(high, equity)
        if high:
            max_dd = max(max_dd, high / max(equity, 1e-12) - 1.0)
    return {
        "total_return": float(last / first - 1.0) if first else 0.0,
        "daily_return": float(payloads[-1].get("daily_return", 0.0) or 0.0),
        "max_drawdown": float(max_dd),
        "realized_pnl": float(payloads[-1].get("realized_pnl", 0.0) or 0.0),
        "unrealized_pnl": float(payloads[-1].get("unrealized_pnl", 0.0) or 0.0),
        "total_fees": float(payloads[-1].get("fees", 0.0) or 0.0),
        "total_stamp_duty": float(payloads[-1].get("taxes", 0.0) or 0.0),
        "total_transfer_fee": 0.0,
        "total_slippage": 0.0,
        "corporate_action_cash": float(payloads[-1].get("corporate_action_cash", 0.0) or 0.0),
        "turnover": 0.0,
        "cash_drag": float(payloads[-1].get("cash", 0.0) / last) if last else 0.0,
        "unsettled_cash_ratio": float(abs(payloads[-1].get("unsettled_cash", 0.0) or 0.0) / last) if last else 0.0,
        "frozen_cash_ratio": float(abs(payloads[-1].get("frozen_cash", 0.0) or 0.0) / last) if last else 0.0,
    }

import json
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def write_settlement_report(account_state, output_dir: str | Path, as_of_date: str, prices_by_date: dict[str, dict[str, float]] | None = None, profile_name: str = "cn_ashare_paper_default") -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    events = [dict(event) for event in getattr(account_state, "settlement_events", []) or account_state.settlement_ledger]
    cash_buckets = update_cash_buckets(account_state, as_of_date)
    availability = update_position_availability(account_state, as_of_date)
    nav_records = build_account_nav_series(account_state, prices_by_date=prices_by_date)
    performance = compute_account_performance(nav_records)
    reconciliation = reconcile_account_state(account_state, events, getattr(account_state, "position_lots", []), nav_records=nav_records, as_of_date=as_of_date)
    fee_tax = _fee_tax_summary(events)
    report = SettlementReport(
        account_id=account_state.account_id,
        as_of_date=as_of_date,
        settlement_aware=True,
        settlement_profile=profile_name,
        pending_settlement_event_count=sum(1 for event in events if event.get("status") == "pending"),
        failed_settlement_event_count=sum(1 for event in events if event.get("status") == "failed"),
        cash_buckets=cash_buckets.to_dict(),
        position_count=len(account_state.positions),
        position_lot_count=len(getattr(account_state, "position_lots", []) or []),
        realized_pnl=float(sum(record.get("realized_pnl", 0.0) for record in getattr(account_state, "realized_pnl_ledger", []) or [])),
        unrealized_pnl=float(sum(position.unrealized_pnl for position in account_state.positions.values())),
        nav_difference=float(reconciliation.nav_difference),
        fee_tax_total=float(fee_tax["fee_tax_total"]),
        reconciliation_error_count=sum(1 for issue in reconciliation.issues if issue.severity in {"error", "blocker"}),
    )
    paths = {
        "settlement_report_path": str(write_json_artifact(target / "settlement_report.json", report.to_dict(), "settlement_report", "settlement_engine")),
        "settlement_events_path": str(write_jsonl_artifact(target / "settlement_events.jsonl", events, "settlement_events", "settlement_engine")),
        "cash_buckets_path": str(write_jsonl_artifact(target / "cash_buckets.jsonl", [cash_buckets.to_dict()], "cash_buckets", "settlement_engine")),
        "position_lots_path": str(write_jsonl_artifact(target / "position_lots.jsonl", getattr(account_state, "position_lots", []) or [], "position_lots", "settlement_engine")),
        "position_availability_path": str(write_jsonl_artifact(target / "position_availability.jsonl", [record.to_dict() for record in availability], "position_availability", "settlement_engine")),
        "realized_pnl_path": str(write_jsonl_artifact(target / "realized_pnl.jsonl", getattr(account_state, "realized_pnl_ledger", []) or [], "realized_pnl", "settlement_engine")),
        "account_nav_path": str(write_jsonl_artifact(target / "account_nav.jsonl", [record.to_dict() for record in nav_records], "account_nav", "settlement_engine")),
        "account_performance_report_path": str(write_json_artifact(target / "account_performance_report.json", performance, "account_performance_report", "settlement_engine")),
        "account_reconciliation_report_path": str(write_json_artifact(target / "account_reconciliation_report.json", reconciliation.to_dict(), "account_reconciliation_report", "settlement_engine")),
        "fee_tax_report_path": str(write_json_artifact(target / "fee_tax_report.json", fee_tax, "fee_tax_report", "settlement_engine")),
    }
    report_payload = report.to_dict() | {"paths": paths}
    write_json_artifact(target / "settlement_report.json", report_payload, "settlement_report", "settlement_engine")
    (target / "settlement_report.md").write_text(_markdown(report_payload, performance, reconciliation.to_dict()), encoding="utf-8")
    paths["settlement_report_md_path"] = str(target / "settlement_report.md")
    return paths


def _fee_tax_summary(events: list[dict[str, Any]]) -> dict[str, float]:
    keys = ["commission", "stamp_duty", "transfer_fee", "slippage", "market_impact", "other_fee", "total"]
    summary = {key: 0.0 for key in keys}
    for event in events:
        fee_tax = event.get("fee_tax") or {}
        for key in keys:
            summary[key] += float(fee_tax.get(key, 0.0) or 0.0)
    summary["fee_tax_total"] = summary["total"]
    summary["total_fee_tax"] = summary["total"]
    return summary


def _markdown(report: dict[str, Any], performance: dict[str, Any], reconciliation: dict[str, Any]) -> str:
    lines = [
        "# Settlement Report",
        "",
        f"- account_id: {report.get('account_id')}",
        f"- as_of_date: {report.get('as_of_date')}",
        f"- settlement_profile: {report.get('settlement_profile')}",
        f"- pending events: {report.get('pending_settlement_event_count')}",
        f"- failed events: {report.get('failed_settlement_event_count')}",
        f"- realized_pnl: {report.get('realized_pnl')}",
        f"- unrealized_pnl: {report.get('unrealized_pnl')}",
        f"- nav_difference: {report.get('nav_difference')}",
        "",
        "## Performance",
        "```json",
        json.dumps(performance, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Reconciliation",
        "```json",
        json.dumps(reconciliation, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    return "\n".join(lines) + "\n"

import argparse
import json
from pathlib import Path
from typing import Any

from auto_alpha.data.pit.corporate_actions.report import read_jsonl
from auto_alpha.execution.trading.paper import LocalPaperAccount



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local settlement accounting.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["apply-fills", "settle", "precheck-orders", "reconcile-account", "build-nav", "report", "smoke"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--data-dir", required=True)
        cmd.add_argument("--account-dir", required=True)
        cmd.add_argument("--settlement-dir", required=True)
        cmd.add_argument("--fills-path")
        cmd.add_argument("--broker-store-dir")
        cmd.add_argument("--broker-batch-id")
        cmd.add_argument("--orders-path")
        cmd.add_argument("--corporate-action-dir")
        cmd.add_argument("--corporate-action-ledger-path")
        cmd.add_argument("--prices-path")
        cmd.add_argument("--trade-date")
        cmd.add_argument("--as-of-date", default="20240104")
        cmd.add_argument(
            "--profile",
            choices=["cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"],
            default="cn_ashare_paper_default",
        )
        cmd.add_argument("--cost-basis-method", choices=["average", "fifo"], default="average")
        cmd.add_argument("--allow-unsettled-cash-for-buy", action="store_true")
        cmd.add_argument("--allow-unsettled-shares-for-sell", action="store_true")
        cmd.add_argument("--enforce-available-cash", action="store_true")
        cmd.add_argument("--enforce-available-shares", action="store_true")
        cmd.add_argument("--settle-through-date")
        cmd.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    account = LocalPaperAccount(args.account_dir)
    profile = load_settlement_profile(
        args.profile,
        cost_basis_method=args.cost_basis_method,
        allow_unsettled_cash_for_buy=args.allow_unsettled_cash_for_buy,
        allow_unsettled_shares_for_sell=args.allow_unsettled_shares_for_sell,
    )
    try:
        payload = _run(args, account, profile)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _run(args: argparse.Namespace, account: LocalPaperAccount, profile) -> dict[str, Any]:
    if args.command == "apply-fills":
        fills = read_jsonl(args.fills_path) if args.fills_path else []
        before = len(account.load_state().settlement_events)
        updated = account.apply_fills_settlement_aware(
            fills,
            data_dir=args.data_dir,
            trade_date=args.trade_date or args.as_of_date,
            profile=profile.profile_name,
            prices=_load_prices(args),
            cost_basis_method=profile.cost_basis_method,
        )
        account.save_state(updated)
        paths = write_settlement_report(updated, args.settlement_dir, args.trade_date or args.as_of_date, profile_name=profile.profile_name)
        return {"events": len(updated.settlement_events) - before, "account_cash": updated.cash, "paths": paths}
    if args.command == "settle":
        updated = account.settle(args.settle_through_date or args.as_of_date, prices=_load_prices(args), profile=profile.profile_name)
        paths = write_settlement_report(updated, args.settlement_dir, args.settle_through_date or args.as_of_date, profile_name=profile.profile_name)
        return {"pending_events": sum(event.get("status") == "pending" for event in updated.settlement_events), "cash": updated.cash, "paths": paths}
    if args.command == "precheck-orders":
        orders = read_jsonl(args.orders_path) if args.orders_path else []
        return precheck_orders_against_availability(account.load_state(), orders, prices=_load_prices(args), profile=profile)
    if args.command == "reconcile-account":
        state = account.load_state()
        report = reconcile_account_state(state, as_of_date=args.as_of_date)
        paths = write_settlement_report(state, args.settlement_dir, args.as_of_date, profile_name=profile.profile_name)
        return report.to_dict() | {"paths": paths}
    if args.command == "build-nav":
        state = account.load_state()
        nav = build_account_nav_series(state, prices_by_date={args.as_of_date: _load_prices(args)})
        updated = account.save_state(_replace_account_nav(state, nav))
        paths = write_settlement_report(updated, args.settlement_dir, args.as_of_date, profile_name=profile.profile_name)
        return {"nav_records": [record.to_dict() for record in nav], "paths": paths}
    if args.command == "report":
        return {"paths": write_settlement_report(account.load_state(), args.settlement_dir, args.as_of_date, profile_name=profile.profile_name)}
    if args.command == "smoke":
        return _smoke(args, account, profile)
    raise ValueError(f"unsupported command: {args.command}")


def _smoke(args: argparse.Namespace, account: LocalPaperAccount, profile) -> dict[str, Any]:
    if account.load_state().initial_cash <= 0:
        account.reset(1000000.0)
    daily = read_jsonl(Path(args.data_dir) / "daily_bars" / "records.jsonl")
    if not daily:
        raise ValueError("daily_bars is empty")
    first = sorted(daily, key=lambda row: (row["trade_date"], row["ts_code"]))[0]
    fill = {
        "trade_date": first["trade_date"],
        "ts_code": first["ts_code"],
        "side": "BUY",
        "price": float(first["close"]),
        "shares": 100,
        "value": float(first["close"]) * 100,
        "cost": 5.0,
        "status": "FILLED",
        "broker_fill_id": "settlement_smoke_buy",
    }
    path = Path(args.settlement_dir) / "smoke_fills.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fill, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    args.fills_path = str(path)
    args.trade_date = first["trade_date"]
    return _run(argparse.Namespace(**{**vars(args), "command": "apply-fills"}), account, profile)


def _replace_account_nav(state, nav):
    from dataclasses import replace

    return replace(state, account_nav=[record.to_dict() for record in nav])


def _load_prices(args: argparse.Namespace) -> dict[str, float]:
    if args.prices_path and Path(args.prices_path).exists():
        payload = json.loads(Path(args.prices_path).read_text(encoding="utf-8"))
        return {str(key): float(value) for key, value in payload.items()}
    data_dir = Path(args.data_dir)
    date = args.as_of_date or args.trade_date
    prices: dict[str, float] = {}
    path = data_dir / "daily_bars" / "records.jsonl"
    if path.exists():
        for record in read_jsonl(path):
            if not date or str(record.get("trade_date")) == date:
                prices[str(record.get("ts_code"))] = float(record.get("close") or 0.0)
    return prices


if __name__ == "__main__":
    raise SystemExit(main())

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
