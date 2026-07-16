"""Strict next-open event-ledger simulator for Task 055-A."""
from __future__ import annotations

from dataclasses import replace
import math
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .models import (
    CashBuckets,
    CorporateActionEvent,
    Fill,
    LedgerState,
    NavRecord,
    Order,
    PositionLot,
    Rejection,
    SettlementEvent,
    SimulationResult,
)
from .policy import BASELINE, ScenarioPolicy, get_scenario_policy, stable_top_n_equal_weight, strict_true_mask


class SimulationDataBlocker(RuntimeError):
    """Raised when a real security-date cannot be valued or executed safely."""


def _matrix(values: Any, shape: tuple[int, int], name: str) -> np.ndarray:
    matrix = np.asarray(values, dtype=float)
    if matrix.shape != shape:
        raise ValueError(f"{name} shape {matrix.shape} does not match {shape}")
    return matrix


def _lot_round(shares: float, lot_size: int) -> int:
    if not math.isfinite(float(shares)) or shares <= 0:
        return 0
    return int(float(shares) // lot_size) * lot_size


def _event(kind: str, index: int, **payload: Any) -> dict[str, Any]:
    return {"sequence": 0, "type": kind, "index": int(index), **payload}


class EventLedgerSimulator:
    """Simulate close decisions and next-open fills without future inputs."""

    def __init__(
        self,
        policy: ScenarioPolicy | Mapping[str, Any] | str = BASELINE,
        *,
        initial_cash: float | None = None,
    ) -> None:
        if isinstance(policy, str):
            self.policy = get_scenario_policy(policy)
        elif isinstance(policy, Mapping):
            self.policy = ScenarioPolicy(**dict(policy))
        else:
            self.policy = policy
        self.initial_cash = float(self.policy.initial_aum if initial_cash is None else initial_cash)
        if self.initial_cash < 0 or not math.isfinite(self.initial_cash):
            raise ValueError("initial_cash must be finite and non-negative")

    def run(
        self,
        market: Mapping[str, Any],
        scores: Any,
        *,
        masks: Mapping[str, Any] | None = None,
        corporate_actions: Sequence[Mapping[str, Any]] = (),
        initial_positions: Mapping[str, int] | None = None,
        diagnostic_mark_observer: Callable[[int, str, str, Mapping[str, int], np.ndarray], None] | None = None,
    ) -> SimulationResult:
        dates = [str(item) for item in self._market_value(market, "dates", "trade_dates")]
        assets = [str(item) for item in self._market_value(market, "assets", "ts_codes")]
        shape = (len(dates), len(assets))
        open_price = _matrix(self._market_value(market, "open", "open_price", "open_prices"), shape, "open")
        close_source = market.get("close", market.get("close_price", market.get("close_prices", open_price)))
        close_price = _matrix(close_source, shape, "close")
        valuation_open = _matrix(market.get("valuation_open", open_price), shape, "valuation_open")
        valuation_close = _matrix(market.get("valuation_close", close_price), shape, "valuation_close")
        score_matrix = _matrix(scores, shape, "scores")
        adv = _matrix(
            self._market_value(market, "adv", "lagged_adv", "average_daily_volume"),
            shape,
            "adv",
        )
        buy_mask, sell_mask, selection_mask = self._resolve_masks(masks, shape)
        actions = self._normalize_actions(corporate_actions, dates, assets)
        actions_by_index: dict[int, list[CorporateActionEvent]] = {}
        for action in actions:
            actions_by_index.setdefault(action.effective_index, []).append(action)

        state = LedgerState(cash=CashBuckets(self.initial_cash))
        for asset, shares_value in (initial_positions or {}).items():
            shares = int(shares_value)
            if asset not in assets or shares < 0 or shares % self.policy.lot_size:
                raise ValueError("initial positions must use known assets and integer board lots")
            if shares:
                state.lots.setdefault(asset, []).append(
                    PositionLot(f"initial:{asset}", asset, shares, -1, 0, 0.0, "initial")
                )

        orders: list[Order] = []
        fills: list[Fill] = []
        rejections: list[Rejection] = []
        settlements: list[SettlementEvent] = []
        nav_records: list[NavRecord] = []
        ledger: list[dict[str, Any]] = []
        pending_orders: dict[int, list[Order]] = {}
        last_open_post: float | None = None

        for index, date in enumerate(dates):
            self._settle(index, state, settlements, ledger)
            self._apply_actions(index, actions_by_index.get(index, ()), state, settlements, ledger)
            self._notify_mark_observer(
                diagnostic_mark_observer,
                index,
                date,
                "open_pretrade",
                state,
                assets,
                valuation_open[index],
            )
            try:
                positions_open = self._position_value(state, assets, valuation_open[index])
            except ValueError as error:
                raise SimulationDataBlocker(f"valuation_open_blocked:{date}:{index}:{error}") from error
            open_pre = state.cash.total + positions_open
            day_cost = 0.0

            for order in pending_orders.pop(index, ()):
                fill, rejection = self._execute_order(
                    order,
                    index,
                    date,
                    assets,
                    open_price[index],
                    adv[index - 1],
                    buy_mask[index],
                    sell_mask[index],
                    state,
                    settlements,
                    ledger,
                )
                if fill is not None:
                    fills.append(fill)
                    day_cost += fill.total_cost
                if rejection is not None:
                    rejections.append(rejection)

            positions_open_post = self._position_value(state, assets, valuation_open[index])
            open_post = state.cash.total + positions_open_post
            self._notify_mark_observer(
                diagnostic_mark_observer,
                index,
                date,
                "close",
                state,
                assets,
                valuation_close[index],
            )
            try:
                positions_close = self._position_value(state, assets, valuation_close[index])
            except ValueError as error:
                raise SimulationDataBlocker(f"valuation_close_blocked:{date}:{index}:{error}") from error
            close_nav = state.cash.total + positions_close
            open_return = None if last_open_post in (None, 0.0) else open_pre / last_open_post - 1.0
            nav_records.append(
                NavRecord(
                    index=index,
                    date=date,
                    open_pre=open_pre,
                    open_post=open_post,
                    close=close_nav,
                    prior_open_post=last_open_post,
                    open_to_open_return=open_return,
                    positions_open=positions_open,
                    positions_close=positions_close,
                    cash_total=state.cash.total,
                    available_cash=state.cash.available,
                    unsettled_cash=state.cash.unsettled_receivable - state.cash.unsettled_payable,
                    daily_cost=day_cost,
                )
            )
            ledger.append(
                _event(
                    "nav",
                    index,
                    date=date,
                    open_pre=open_pre,
                    open_post=open_post,
                    close=close_nav,
                    open_to_open_return=open_return,
                )
            )
            last_open_post = open_post

            if index + 1 < len(dates):
                new_orders = self._decide_orders(
                    index,
                    assets,
                    valuation_close[index],
                    score_matrix[index],
                    selection_mask[index],
                    state,
                    close_nav,
                )
                orders.extend(new_orders)
                pending_orders[index + 1] = new_orders
                for order in new_orders:
                    ledger.append(_event("order", index, **order.to_dict()))

        for sequence, item in enumerate(ledger, start=1):
            item["sequence"] = sequence
        return SimulationResult(
            dates=dates,
            assets=assets,
            orders=orders,
            fills=fills,
            rejections=rejections,
            settlements=settlements,
            corporate_actions=actions,
            nav=nav_records,
            final_cash=state.cash,
            final_positions=state.position_snapshot(len(dates) - 1),
            event_ledger=ledger,
        )

    simulate = run

    @staticmethod
    def _market_value(market: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in market:
                return market[key]
        raise KeyError(f"missing market field; expected one of {keys}")

    @staticmethod
    def _resolve_masks(
        masks: Mapping[str, Any] | Any | None,
        shape: tuple[int, int],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if masks is not None and not isinstance(masks, Mapping):
            common_mask = strict_true_mask(masks, shape)
            return common_mask.copy(), common_mask.copy(), common_mask.copy()
        source = masks or {}
        common = source.get("tradable", source.get("tradable_mask"))
        buy_source = source.get("buy", source.get("buy_mask", common))
        sell_source = source.get("sell", source.get("sell_mask", common))
        select_source = source.get("select", source.get("selection_mask", common))
        return (
            strict_true_mask(buy_source, shape),
            strict_true_mask(sell_source, shape),
            strict_true_mask(select_source, shape),
        )

    def _decide_orders(
        self,
        index: int,
        assets: list[str],
        close_prices: np.ndarray,
        scores: np.ndarray,
        selection_mask: np.ndarray,
        state: LedgerState,
        close_nav: float,
    ) -> list[Order]:
        price_known = np.isfinite(close_prices) & (close_prices > 0)
        weights = stable_top_n_equal_weight(
            scores,
            assets,
            eligible=selection_mask & price_known,
            top_n=self.policy.top_n,
        )
        result: list[Order] = []
        for asset_index, asset in enumerate(assets):
            target = _lot_round(
                weights[asset_index] * max(close_nav, 0.0) / close_prices[asset_index]
                if price_known[asset_index]
                else 0.0,
                self.policy.lot_size,
            )
            current = state.total_shares(asset)
            delta = target - current
            if delta == 0:
                continue
            side = "BUY" if delta > 0 else "SELL"
            result.append(
                Order(
                    order_id=f"order:{index}:{asset}",
                    decision_index=index,
                    execution_index=index + 1,
                    asset=asset,
                    side=side,
                    requested_shares=abs(delta),
                    target_shares=target,
                    decision_price=float(close_prices[asset_index]),
                    target_weight=float(weights[asset_index]),
                )
            )
        return result

    def _execute_order(
        self,
        order: Order,
        index: int,
        trade_date: str,
        assets: list[str],
        open_prices: np.ndarray,
        lagged_adv_row: np.ndarray,
        buy_mask: np.ndarray,
        sell_mask: np.ndarray,
        state: LedgerState,
        settlements: list[SettlementEvent],
        ledger: list[dict[str, Any]],
    ) -> tuple[Fill | None, Rejection | None]:
        asset_index = assets.index(order.asset)
        price = float(open_prices[asset_index])
        allowed = bool(buy_mask[asset_index] if order.side == "BUY" else sell_mask[asset_index])
        if not allowed:
            return None, self._reject(order, index, "mask_unknown_or_false", ledger)
        if not math.isfinite(price) or price <= 0:
            return None, self._reject(order, index, "invalid_open_price", ledger)

        lagged_adv = float(lagged_adv_row[asset_index])
        capacity = _lot_round(
            max(lagged_adv, 0.0) * self.policy.adv_participation
            if math.isfinite(lagged_adv)
            else 0.0,
            self.policy.lot_size,
        )
        executable = min(order.requested_shares, capacity)
        if order.side == "SELL":
            executable = min(executable, state.available_shares(order.asset, index))
        else:
            executable = min(executable, self._cash_affordable_shares(state.cash.available, price, trade_date))
        executable = _lot_round(executable, self.policy.lot_size)
        if executable <= 0:
            if capacity <= 0:
                reason = "lagged_adv_capacity"
            elif order.side == "SELL":
                reason = "t_plus_one_or_insufficient_available_shares"
            else:
                reason = "insufficient_available_cash"
            return None, self._reject(order, index, reason, ledger)

        status = "FILLED" if executable == order.requested_shares else "PARTIAL"
        notional = price * executable
        costs = self._costs(order.side, notional, trade_date)
        fill = Fill(
            fill_id=f"fill:{index}:{order.asset}:{order.side}",
            order_id=order.order_id,
            execution_index=index,
            asset=order.asset,
            side=order.side,
            requested_shares=order.requested_shares,
            filled_shares=executable,
            price=price,
            notional=notional,
            commission=costs["commission"],
            stamp_duty=costs["stamp_duty"],
            transfer_fee=costs["transfer_fee"],
            slippage=costs["slippage"],
            impact=costs["impact"],
            total_cost=costs["total"],
            status=status,
            capacity_shares=capacity,
            lagged_adv=lagged_adv,
        )
        if order.side == "BUY":
            required = notional + costs["total"]
            state.cash.frozen += required
            state.cash.available -= required
            state.cash.withdrawable -= required
            ledger.append(_event("cash_frozen", index, order_id=order.order_id, amount=required))
            state.cash.frozen -= required
            state.lots.setdefault(order.asset, []).append(
                PositionLot(
                    lot_id=f"lot:{fill.fill_id}",
                    asset=order.asset,
                    shares=executable,
                    acquired_index=index,
                    available_index=index + self.policy.buy_share_lag,
                    unit_cost=required / executable,
                )
            )
            settlement = SettlementEvent(
                event_id=f"settle:shares:{fill.fill_id}",
                event_type="buy_shares_available",
                created_index=index,
                settle_index=index + self.policy.buy_share_lag,
                asset=order.asset,
                shares=executable,
                source_id=fill.fill_id,
            )
        else:
            state.frozen_shares[order.asset] = state.frozen_shares.get(order.asset, 0) + executable
            ledger.append(_event("shares_frozen", index, order_id=order.order_id, shares=executable))
            self._consume_available_lots(state, order.asset, executable, index)
            state.frozen_shares[order.asset] -= executable
            net_receivable = notional - costs["total"]
            state.cash.unsettled_receivable += net_receivable
            settlement = SettlementEvent(
                event_id=f"settle:cash:{fill.fill_id}",
                event_type="sell_cash_available",
                created_index=index,
                settle_index=index + self.policy.sell_cash_lag,
                asset=order.asset,
                cash_amount=net_receivable,
                source_id=fill.fill_id,
            )
        state.pending_settlements.append(settlement)
        settlements.append(settlement)
        ledger.append(_event("fill", index, **fill.to_dict()))
        if status == "PARTIAL":
            ledger.append(
                _event(
                    "partial",
                    index,
                    order_id=order.order_id,
                    requested_shares=order.requested_shares,
                    filled_shares=executable,
                )
            )
        return fill, None

    def _cash_affordable_shares(self, cash: float, price: float, trade_date: str) -> int:
        max_lots = int(max(cash, 0.0) // (price * self.policy.lot_size))
        while max_lots > 0:
            shares = max_lots * self.policy.lot_size
            if price * shares + self._costs("BUY", price * shares, trade_date)["total"] <= cash + 1e-9:
                return shares
            max_lots -= 1
        return 0

    def _costs(self, side: str, notional: float, trade_date: str) -> dict[str, float]:
        if self.policy.zero_all_costs:
            return {key: 0.0 for key in ("commission", "stamp_duty", "transfer_fee", "slippage", "impact", "total")}
        multiplier = self.policy.modeled_cost_multiplier
        commission = max(notional * self.policy.commission_rate, self.policy.minimum_commission) * multiplier
        if self.policy.fee_schedule_id == "cn_ashare_historical_fees_modeled_execution_v1":
            stamp_rate = 0.0005 if trade_date >= "20230828" else 0.001
            transfer_rate = 0.00001 if trade_date >= "20220429" else 0.00002
        else:
            stamp_rate = self.policy.stamp_duty_rate
            transfer_rate = self.policy.transfer_fee_rate
        stamp = notional * stamp_rate if side == "SELL" else 0.0
        transfer = notional * transfer_rate
        slippage = notional * self.policy.slippage_bps * multiplier / 10_000.0
        impact = notional * self.policy.impact_bps * multiplier / 10_000.0
        total = commission + stamp + transfer + slippage + impact
        return {
            "commission": float(commission),
            "stamp_duty": float(stamp),
            "transfer_fee": float(transfer),
            "slippage": float(slippage),
            "impact": float(impact),
            "total": float(total),
        }

    @staticmethod
    def _consume_available_lots(state: LedgerState, asset: str, shares: int, index: int) -> None:
        remaining = int(shares)
        lots = state.lots.get(asset, [])
        for lot in sorted(lots, key=lambda item: (item.available_index, item.acquired_index, item.lot_id)):
            if lot.available_index > index or remaining <= 0:
                continue
            used = min(lot.shares, remaining)
            lot.shares -= used
            remaining -= used
        state.lots[asset] = [lot for lot in lots if lot.shares > 0]
        if remaining:
            raise RuntimeError("strict ledger attempted to consume unavailable shares")

    @staticmethod
    def _position_value(state: LedgerState, assets: list[str], prices: np.ndarray) -> float:
        total = 0.0
        for index, asset in enumerate(assets):
            shares = state.total_shares(asset)
            price = float(prices[index])
            if shares and (not math.isfinite(price) or price <= 0):
                raise ValueError(f"cannot mark held asset {asset} with unknown price")
            if shares:
                total += shares * price
        return float(total)

    @staticmethod
    def _notify_mark_observer(
        observer: Callable[[int, str, str, Mapping[str, int], np.ndarray], None] | None,
        index: int,
        date: str,
        reporting_point: str,
        state: LedgerState,
        assets: list[str],
        prices: np.ndarray,
    ) -> None:
        if observer is None:
            return
        held = {}
        for asset in assets:
            shares = state.total_shares(asset)
            if shares > 0:
                held[asset] = shares
        observed_prices = np.array(prices, dtype=float, copy=True)
        observed_prices.setflags(write=False)
        observer(index, date, reporting_point, held, observed_prices)

    @staticmethod
    def _reject(order: Order, index: int, reason: str, ledger: list[dict[str, Any]]) -> Rejection:
        rejection = Rejection(
            order_id=order.order_id,
            execution_index=index,
            asset=order.asset,
            side=order.side,
            requested_shares=order.requested_shares,
            reason=reason,
        )
        ledger.append(_event("rejection", index, **rejection.to_dict()))
        return rejection

    @staticmethod
    def _settle(
        index: int,
        state: LedgerState,
        settlements: list[SettlementEvent],
        ledger: list[dict[str, Any]],
    ) -> None:
        pending: list[SettlementEvent] = []
        for event in state.pending_settlements:
            if event.settle_index > index:
                pending.append(event)
                continue
            if event.event_type in {"sell_cash_available", "cash_dividend_available"}:
                state.cash.unsettled_receivable -= event.cash_amount
                state.cash.available += event.cash_amount
                state.cash.withdrawable += event.cash_amount
            settled = replace(event, status="settled")
            settlement_index = next(
                offset
                for offset, candidate in enumerate(settlements)
                if candidate.event_id == event.event_id
            )
            settlements[settlement_index] = settled
            ledger.append(_event("settlement", index, **settled.to_dict()))
        state.pending_settlements = pending

    @staticmethod
    def _normalize_actions(
        actions: Sequence[Mapping[str, Any]],
        dates: list[str],
        assets: list[str],
    ) -> list[CorporateActionEvent]:
        date_index = {date: index for index, date in enumerate(dates)}
        result: list[CorporateActionEvent] = []
        for offset, raw in enumerate(actions):
            asset = str(raw.get("asset", raw.get("ts_code", "")))
            if asset not in assets:
                raise ValueError(f"unknown corporate-action asset: {asset}")
            effective_raw = raw.get("effective_index", raw.get("ex_index", raw.get("ex_date")))
            effective = date_index[str(effective_raw)] if str(effective_raw) in date_index else int(effective_raw)
            pay_raw = raw.get("pay_index") or raw.get("pay_date") or effective
            pay_index = date_index[str(pay_raw)] if str(pay_raw) in date_index else int(pay_raw)
            share_ratio = float(raw.get("share_ratio", raw.get("split_ratio", 1.0)))
            share_ratio += float(
                raw.get(
                    "stock_dividend_ratio",
                    (raw.get("stk_bo_rate") or 0.0) + (raw.get("stk_co_rate") or 0.0),
                )
            )
            if share_ratio <= 0 or not math.isfinite(share_ratio):
                raise ValueError("corporate-action share_ratio must be positive and finite")
            result.append(
                CorporateActionEvent(
                    action_id=str(raw.get("action_id", f"action:{effective}:{asset}:{offset}")),
                    effective_index=effective,
                    asset=asset,
                    cash_dividend_per_share=float(
                        raw.get("cash_dividend_per_share", raw.get("cash_div") or 0.0)
                    ),
                    share_ratio=share_ratio,
                    pay_index=pay_index,
                )
            )
        return sorted(result, key=lambda item: (item.effective_index, item.asset, item.action_id))

    @staticmethod
    def _apply_actions(
        index: int,
        actions: Sequence[CorporateActionEvent],
        state: LedgerState,
        settlements: list[SettlementEvent],
        ledger: list[dict[str, Any]],
    ) -> None:
        for action in actions:
            pre_shares = state.total_shares(action.asset)
            if action.share_ratio != 1.0 and pre_shares:
                for lot in state.lots.get(action.asset, ()):
                    adjusted = int(math.floor(lot.shares * action.share_ratio + 1e-12))
                    if adjusted > 0:
                        lot.unit_cost *= lot.shares / adjusted
                    lot.shares = adjusted
                ledger.append(
                    _event(
                        "corporate_action_shares",
                        index,
                        action_id=action.action_id,
                        asset=action.asset,
                        pre_shares=pre_shares,
                        post_shares=state.total_shares(action.asset),
                        share_ratio=action.share_ratio,
                    )
                )
            dividend = pre_shares * action.cash_dividend_per_share
            if dividend:
                settle_index = int(action.pay_index if action.pay_index is not None else index)
                event = SettlementEvent(
                    event_id=f"settle:dividend:{action.action_id}",
                    event_type="cash_dividend_available",
                    created_index=index,
                    settle_index=settle_index,
                    asset=action.asset,
                    cash_amount=dividend,
                    source_id=action.action_id,
                )
                settlements.append(event)
                if settle_index <= index:
                    state.cash.available += dividend
                    state.cash.withdrawable += dividend
                    settlements[-1] = replace(event, status="settled")
                    ledger.append(_event("settlement", index, **settlements[-1].to_dict()))
                else:
                    state.cash.unsettled_receivable += dividend
                    state.pending_settlements.append(event)
            ledger.append(_event("corporate_action", index, **action.to_dict()))


StrictEventLedgerSimulator = EventLedgerSimulator
Task055ASimulator = EventLedgerSimulator


def simulate_event_ledger(
    market: Mapping[str, Any],
    scores: Any,
    *,
    masks: Mapping[str, Any] | None = None,
    corporate_actions: Sequence[Mapping[str, Any]] = (),
    initial_cash: float | None = None,
    initial_positions: Mapping[str, int] | None = None,
    policy: ScenarioPolicy | Mapping[str, Any] | str = BASELINE,
    diagnostic_mark_observer: Callable[[int, str, str, Mapping[str, int], np.ndarray], None] | None = None,
) -> SimulationResult:
    return EventLedgerSimulator(policy, initial_cash=initial_cash).run(
        market,
        scores,
        masks=masks,
        corporate_actions=corporate_actions,
        initial_positions=initial_positions,
        diagnostic_mark_observer=diagnostic_mark_observer,
    )


run_simulation = simulate_event_ledger
