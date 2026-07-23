from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from task_055_a.bundle import load_simulation_bundle, validate_simulation_bundle
from task_055_a.policy import PREREGISTERED_SCENARIOS
from task_055_f.valuation import METHOD_NAMES, load_valuation_projection
from task_055_g.causal import validate_fee_aware_causal_frontier

from .fee import FeeProjectionCalculator
from .io import canonical_hash


class IndependentCausalError(RuntimeError):
    pass


@dataclass
class _Cash:
    available: float
    frozen: float = 0.0
    unsettled_receivable: float = 0.0
    unsettled_payable: float = 0.0
    withdrawable: float | None = None

    def __post_init__(self) -> None:
        if self.withdrawable is None:
            self.withdrawable = self.available

    @property
    def total(self) -> float:
        return self.available + self.frozen + self.unsettled_receivable - self.unsettled_payable


def independently_replay_causal_frontier(
    *,
    simulation_bundle_manifest: str | Path,
    valuation_projection_manifest: str | Path,
    fee_schedule_manifest: str | Path,
    producer_causal_manifest: str | Path,
) -> dict[str, Any]:
    bundle_manifest = validate_simulation_bundle(simulation_bundle_manifest, require_ready=True)
    bundle = load_simulation_bundle(simulation_bundle_manifest)
    projection = load_valuation_projection(valuation_projection_manifest)
    producer = validate_fee_aware_causal_frontier(producer_causal_manifest)
    prepared = _prepare(bundle, projection)
    results = {}
    for mode in ("net_commission_3bp", "all_in_commission_3bp"):
        calculator = FeeProjectionCalculator(fee_schedule_manifest, commission_mode=mode)
        results[mode] = _trace(bundle_manifest, prepared, projection, calculator)
    net = results["net_commission_3bp"]
    if (
        net["run_rows_root"] != producer["run_rows_root"]
        or net["held_mark_root"] != producer["held_mark_root"]
        or net["frontier_root"] != producer["missing_key_root"]
        or net["frontier_count"] != producer["round_one_frontier_count"]
    ):
        raise IndependentCausalError("independent_net_commission_causal_mismatch")
    union = sorted({tuple(item) for mode in results.values() for item in mode["frontier_keys"]})
    semantic = {
        "status": "passed",
        "producer_causal_content_hash": producer["content_hash"],
        "bundle_content_hash": bundle_manifest["content_hash"],
        "commission_modes": {
            name: {
                key: value
                for key, value in result.items()
                if key not in {"run_rows", "held_rows", "frontier_keys"}
            }
            | {"frontier_keys": result["frontier_keys"]}
            for name, result in results.items()
        },
        "frontier_union": union,
        "frontier_union_count": len(union),
        "frontier_union_root": canonical_hash(union),
    }
    return semantic | {"content_hash": canonical_hash(semantic)}


def independently_trace_prepared(
    *,
    bundle_manifest: Mapping[str, Any],
    prepared: Mapping[str, Any],
    projection: Mapping[str, Any],
    calculator: FeeProjectionCalculator,
) -> dict[str, Any]:
    return _trace(bundle_manifest, prepared, projection, calculator)


def prepare_independent_inputs(
    bundle: Mapping[str, Any], projection: Mapping[str, Any]
) -> dict[str, Any]:
    return _prepare(bundle, projection)


def _prepare(bundle: Mapping[str, Any], projection: Mapping[str, Any]) -> dict[str, Any]:
    dates = [str(value) for value in bundle["execution_dates"]]
    signal_dates = [str(value) for value in bundle["trade_dates"]]
    assets = [str(value) for value in bundle["ts_codes"]]
    projection_dates = list(map(str, projection["dates"]))
    projection_assets = list(map(str, projection["assets"]))
    if projection_assets != assets or projection_dates[0] not in dates or projection_dates[-1] not in dates:
        raise IndependentCausalError("independent_projection_axis_not_in_bundle")
    start = dates.index(projection_dates[0])
    end = dates.index(projection_dates[-1]) + 1
    if dates[start:end] != projection_dates:
        raise IndependentCausalError("independent_projection_axis_not_contiguous")
    signal_end = min(len(signal_dates), end)
    signal_count = signal_end - start
    raw = bundle["raw"]
    validity = bundle["raw_validity"]
    open_values = np.asarray(raw["open"], dtype=float).T[start:end]
    close_values = np.asarray(raw["close"], dtype=float).T[start:end]
    vol_values = np.asarray(raw["vol"], dtype=float).T[start:end]
    open_valid = np.asarray(validity["open"], dtype=bool).T[start:end]
    close_valid = np.asarray(validity["close"], dtype=bool).T[start:end]
    vol_valid = np.asarray(validity["vol"], dtype=bool).T[start:end]
    execution_masks = bundle["execution_masks"]
    strict_masks = bundle["strict_masks"]
    buy = _mask(execution_masks["buyable_at_open"], len(dates), len(assets))[start:end]
    sell = _mask(execution_masks["sellable_at_open"], len(dates), len(assets))[start:end]
    common = np.ones_like(buy)
    for name in ("active", "listed", "open_execution_known", "open_execution_value", "suspension_source_covered", "corporate_action_validity"):
        common &= _mask(execution_masks[name], len(dates), len(assets))[start:end]
    membership = _mask(execution_masks["membership"], len(dates), len(assets))[start:end]
    membership_known = _mask(execution_masks["membership_known"], len(dates), len(assets))[start:end]
    excluded = _mask(execution_masks["conservative_open_excluded"], len(dates), len(assets))[start:end]
    gaps = _mask(execution_masks["unexplained_data_gap"], len(dates), len(assets))[start:end]
    buy &= common & membership & membership_known & ~excluded & ~gaps & open_valid
    sell &= common & ~excluded & ~gaps & open_valid
    signal_common = np.ones((signal_count, len(assets)), dtype=bool)
    for name in ("signal_candidate_cells", "membership", "membership_known", "active", "listed", "st_status_known", "st_information_available", "signal_eligible_at_close"):
        signal_common &= _mask(strict_masks[name], len(signal_dates), len(assets))[start:signal_end]
    signal_common &= ~_mask(strict_masks["st_effective"], len(signal_dates), len(assets))[start:signal_end]
    signal_common &= ~_mask(strict_masks["unexplained_data_gap"], len(signal_dates), len(assets))[start:signal_end]
    signal_common &= close_valid[:signal_count]
    factors = {
        factor_id: np.asarray(value, dtype=float)[:, start:signal_end]
        for factor_id, value in bundle["factor_values"].items()
    }
    factor_validity = {
        factor_id: np.asarray(value, dtype=bool)[:, start:signal_end]
        for factor_id, value in bundle["factor_validity"].items()
    }
    return {
        "dates": projection_dates,
        "assets": assets,
        "signal_count": signal_count,
        "open": open_values,
        "close": close_values,
        "adv": _lagged_adv(vol_values, vol_valid),
        "buy": buy,
        "sell": sell,
        "signal_common": signal_common,
        "factor_values": factors,
        "factor_validity": factor_validity,
        "corporate_actions": _actions(bundle.get("corporate_actions") or (), projection_dates, assets),
    }


def _trace(
    bundle_manifest: Mapping[str, Any],
    prepared: Mapping[str, Any],
    projection: Mapping[str, Any],
    calculator: FeeProjectionCalculator,
) -> dict[str, Any]:
    run_rows: list[dict[str, Any]] = []
    held_rows: list[dict[str, Any]] = []
    terminal_counts: Counter[str] = Counter()
    frontier: set[tuple[str, str]] = set()
    modeled = 0
    exact_ids = list(bundle_manifest["exact20_ids"])
    for factor_id in exact_ids:
        scores = np.full((len(prepared["dates"]), len(prepared["assets"])), np.nan)
        selection = np.zeros(scores.shape, dtype=bool)
        scores[: prepared["signal_count"]] = prepared["factor_values"][factor_id].T
        selection[: prepared["signal_count"]] = prepared["factor_validity"][factor_id].T & prepared["signal_common"]
        for scenario_name, policy in PREREGISTERED_SCENARIOS.items():
            run_marks, blocker = _run_one(prepared, projection, scores, selection, policy, calculator)
            terminal = "causal_trace_completed" if blocker is None else "causal_valuation_blocked"
            if blocker is not None:
                frontier.add((blocker["ts_code"], blocker["trade_date"]))
            for row in run_marks:
                row.update({"factor_id": factor_id, "scenario": scenario_name})
                row["row_hash"] = canonical_hash(row)
            held_rows.extend(run_marks)
            modeled += sum(row["method"] == "STALE_VENDOR_DAILY_NON_TRADING_MODELED" for row in run_marks)
            terminal_counts[terminal] += 1
            run = {
                "factor_id": factor_id,
                "scenario": scenario_name,
                "terminal_state": terminal,
                "held_mark_count_before_terminal": len(run_marks),
                "held_mark_root": canonical_hash(run_marks),
                "blocker": blocker,
            }
            run["row_hash"] = canonical_hash(run)
            run_rows.append(run)
    held_rows.sort(key=lambda row: (row["factor_id"], row["scenario"], row["trade_date"], row["reporting_point"], row["ts_code"]))
    frontier_keys = sorted(frontier)
    return {
        "run_rows": run_rows,
        "held_rows": held_rows,
        "run_count": len(run_rows),
        "terminal_counts": dict(sorted(terminal_counts.items())),
        "run_rows_root": canonical_hash(run_rows),
        "held_mark_root": canonical_hash(held_rows),
        "held_mark_count": len(held_rows),
        "authorized_modeled_held_mark_count": modeled,
        "frontier_keys": frontier_keys,
        "frontier_count": len(frontier_keys),
        "frontier_root": canonical_hash(frontier_keys),
    }


def _run_one(prepared: Mapping[str, Any], projection: Mapping[str, Any], scores: np.ndarray, selection: np.ndarray, policy: Any, calculator: FeeProjectionCalculator) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    dates = prepared["dates"]
    assets = prepared["assets"]
    asset_index = {asset: index for index, asset in enumerate(assets)}
    cash = _Cash(float(policy.initial_aum))
    lots: dict[str, list[dict[str, Any]]] = {}
    pending_settlements: list[dict[str, Any]] = []
    pending_orders: dict[int, list[dict[str, Any]]] = {}
    held_rows: list[dict[str, Any]] = []
    actions = prepared["corporate_actions"]
    for day, date in enumerate(dates):
        _settle(day, cash, pending_settlements)
        _apply_actions(day, actions.get(day, ()), cash, lots, pending_settlements)
        blocker = _observe(held_rows, day, date, "open_pretrade", assets, lots, projection, point_index=0)
        if blocker:
            return held_rows, blocker
        for order in pending_orders.pop(day, ()):
            _execute_order(order, day, date, assets, asset_index, cash, lots, pending_settlements, prepared, policy, calculator)
        blocker = _observe(held_rows, day, date, "close", assets, lots, projection, point_index=1)
        if blocker:
            return held_rows, blocker
        close_nav = cash.total + _position_value(lots, assets, np.asarray(projection["valuation_close"])[day])
        if day + 1 < len(dates):
            pending_orders[day + 1] = _decide(day, assets, lots, np.asarray(projection["valuation_close"])[day], scores[day], selection[day], close_nav, policy)
    return held_rows, None


def _observe(rows: list[dict[str, Any]], day: int, date: str, reporting_point: str, assets: list[str], lots: Mapping[str, list[dict[str, Any]]], projection: Mapping[str, Any], *, point_index: int) -> dict[str, Any] | None:
    candidate = []
    values = np.asarray(projection["arrays"]["values"])
    methods = np.asarray(projection["arrays"]["methods"])
    sources = np.asarray(projection["arrays"]["source_date"])
    ages = np.asarray(projection["arrays"]["stale_age"])
    evidence = np.asarray(projection["arrays"]["evidence_id"])
    for index, asset in enumerate(assets):
        shares = _total_shares(lots, asset)
        if shares <= 0:
            continue
        code = int(methods[day, index, point_index])
        price = float(values[day, index, point_index])
        evidence_id = bytes(evidence[day, index, point_index]).decode("ascii").rstrip("\x00")
        source_index = int(sources[day, index, point_index])
        if code == 0 or not evidence_id or source_index < 0 or not math.isfinite(price) or price <= 0:
            point = "open_pretrade" if point_index == 0 else "close"
            reason = _projection_reason(projection, asset, date, point)
            method = METHOD_NAMES.get(code, "UNRESOLVED")
            return {
                "code": "held_position_mark_unavailable",
                "detail": f"explicit_valuation_mark_blocked:{date}:{asset}:{point}:{method}",
                "reason": reason,
                "reporting_point": point,
                "trade_date": date,
                "ts_code": asset,
            }
        candidate.append({
            "ts_code": asset,
            "trade_date": date,
            "reporting_point": reporting_point,
            "shares": shares,
            "mark_price": price,
            "method": METHOD_NAMES[code],
            "source_date": projection["dates"][source_index],
            "stale_age_trade_days": int(ages[day, index, point_index]),
            "evidence_id": evidence_id,
        })
    rows.extend(candidate)
    return None


def _decide(day: int, assets: list[str], lots: Mapping[str, list[dict[str, Any]]], close: np.ndarray, scores: np.ndarray, eligible: np.ndarray, nav: float, policy: Any) -> list[dict[str, Any]]:
    valid = np.isfinite(scores) & np.asarray(eligible, dtype=bool) & np.isfinite(close) & (close > 0)
    selected = [index for index in range(len(assets)) if valid[index]]
    selected.sort(key=lambda index: (-float(scores[index]), assets[index]))
    selected = selected[: int(policy.top_n)]
    weight = 1.0 / len(selected) if selected else 0.0
    selected_set = set(selected)
    orders = []
    for index, asset in enumerate(assets):
        target = _lot_round((weight if index in selected_set else 0.0) * max(nav, 0.0) / close[index], int(policy.lot_size))
        delta = target - _total_shares(lots, asset)
        if delta:
            orders.append({"asset": asset, "side": "BUY" if delta > 0 else "SELL", "shares": abs(delta)})
    return orders


def _execute_order(order: Mapping[str, Any], day: int, date: str, assets: list[str], asset_index: Mapping[str, int], cash: _Cash, lots: dict[str, list[dict[str, Any]]], pending: list[dict[str, Any]], prepared: Mapping[str, Any], policy: Any, calculator: FeeProjectionCalculator) -> None:
    index = asset_index[str(order["asset"])]
    side = str(order["side"])
    if not bool(prepared["buy"][day, index] if side == "BUY" else prepared["sell"][day, index]):
        return
    price = float(prepared["open"][day, index])
    if not math.isfinite(price) or price <= 0:
        return
    lagged_adv = float(prepared["adv"][day - 1, index])
    capacity = _lot_round(max(lagged_adv, 0.0) * float(policy.adv_participation) if math.isfinite(lagged_adv) else 0.0, int(policy.lot_size))
    executable = min(int(order["shares"]), capacity)
    if side == "SELL":
        executable = min(executable, _available_shares(lots, str(order["asset"]), day))
    else:
        executable = min(executable, _affordable(cash.available, price, date, str(order["asset"]), policy, calculator))
    executable = _lot_round(executable, int(policy.lot_size))
    if executable <= 0:
        return
    notional = price * executable
    market = "SSE" if str(order["asset"]).endswith(".SH") else "SZSE"
    fees = calculator.calculate(date=date, market=market, side=side, notional=notional, shares=executable, zero_all_costs=bool(policy.zero_all_costs), modeled_multiplier=float(policy.modeled_cost_multiplier))
    if side == "BUY":
        required = notional + fees["total"]
        cash.available -= required
        cash.withdrawable = float(cash.withdrawable) - required
        lots.setdefault(str(order["asset"]), []).append({"shares": executable, "available_index": day + int(policy.buy_share_lag), "unit_cost": required / executable})
    else:
        _consume(lots, str(order["asset"]), executable, day)
        receivable = notional - fees["total"]
        cash.unsettled_receivable += receivable
        pending.append({"kind": "cash", "settle_index": day + int(policy.sell_cash_lag), "amount": receivable})


def _affordable(cash: float, price: float, date: str, asset: str, policy: Any, calculator: FeeProjectionCalculator) -> int:
    lots = int(max(cash, 0.0) // (price * int(policy.lot_size)))
    market = "SSE" if asset.endswith(".SH") else "SZSE"
    while lots > 0:
        shares = lots * int(policy.lot_size)
        notional = price * shares
        fees = calculator.calculate(date=date, market=market, side="BUY", notional=notional, shares=shares, zero_all_costs=bool(policy.zero_all_costs), modeled_multiplier=float(policy.modeled_cost_multiplier))
        if notional + fees["total"] <= cash + 1e-9:
            return shares
        lots -= 1
    return 0


def _settle(day: int, cash: _Cash, pending: list[dict[str, Any]]) -> None:
    remaining = []
    for event in pending:
        if int(event["settle_index"]) > day:
            remaining.append(event)
            continue
        if event["kind"] in {"cash", "dividend"}:
            amount = float(event["amount"])
            cash.unsettled_receivable -= amount
            cash.available += amount
            cash.withdrawable = float(cash.withdrawable) + amount
    pending[:] = remaining


def _apply_actions(day: int, actions: list[dict[str, Any]], cash: _Cash, lots: dict[str, list[dict[str, Any]]], pending: list[dict[str, Any]]) -> None:
    for action in actions:
        asset = str(action["asset"])
        shares_before = _total_shares(lots, asset)
        ratio = float(action["share_ratio"])
        if shares_before and ratio != 1.0:
            for lot in lots.get(asset, ()):
                adjusted = int(math.floor(int(lot["shares"]) * ratio + 1e-12))
                if adjusted > 0:
                    lot["unit_cost"] = float(lot["unit_cost"]) * int(lot["shares"]) / adjusted
                lot["shares"] = adjusted
        dividend = shares_before * float(action["cash_dividend_per_share"])
        if dividend:
            settle = int(action["pay_index"])
            if settle <= day:
                cash.available += dividend
                cash.withdrawable = float(cash.withdrawable) + dividend
            else:
                cash.unsettled_receivable += dividend
                pending.append({"kind": "dividend", "settle_index": settle, "amount": dividend})


def _actions(rows: Any, dates: list[str], assets: list[str]) -> dict[int, list[dict[str, Any]]]:
    date_index = {date: index for index, date in enumerate(dates)}
    result: dict[int, list[dict[str, Any]]] = {}
    for offset, row in enumerate(rows):
        asset = str(row.get("asset", row.get("ts_code", "")))
        if asset not in assets:
            continue
        raw_effective = row.get("effective_index", row.get("ex_index", row.get("ex_date")))
        normalized = str(raw_effective).replace("-", "")
        if normalized not in date_index:
            continue
        effective = date_index[normalized]
        raw_pay = row.get("pay_index") or row.get("pay_date") or normalized
        pay_normalized = str(raw_pay).replace("-", "")
        pay_index = date_index.get(pay_normalized, effective)
        ratio = float(row.get("share_ratio", row.get("split_ratio", 1.0))) + float(row.get("stock_dividend_ratio", (row.get("stk_bo_rate") or 0.0) + (row.get("stk_co_rate") or 0.0)))
        action = {
            "action_id": str(row.get("action_id", f"action:{effective}:{asset}:{offset}")),
            "asset": asset,
            "share_ratio": ratio,
            "cash_dividend_per_share": float(row.get("cash_dividend_per_share", row.get("cash_div") or 0.0)),
            "pay_index": pay_index,
        }
        result.setdefault(effective, []).append(action)
    return result


def _projection_reason(projection: Mapping[str, Any], asset: str, date: str, point: str) -> str:
    for row in projection.get("blockers") or ():
        if row.get("ts_code") == asset and row.get("trade_date") == date and row.get("reporting_point") == point:
            return str(row.get("reason") or "valuation_projection_blocked")
    return "valuation_projection_blocked"


def _mask(value: Any, dates: int, assets: int) -> np.ndarray:
    array = np.asarray(value, dtype=bool)
    if array.shape == (assets, dates):
        return array.T.copy()
    if array.shape == (dates, assets):
        return array.copy()
    raise IndependentCausalError(f"mask_shape_invalid:{array.shape}:{dates}:{assets}")


def _lagged_adv(volume: np.ndarray, valid: np.ndarray, window: int = 20) -> np.ndarray:
    result = np.full(volume.shape, np.nan)
    for day in range(volume.shape[0]):
        start = max(0, day - window + 1)
        window_values = volume[start : day + 1]
        window_valid = valid[start : day + 1] & np.isfinite(window_values) & (window_values >= 0)
        count = window_valid.sum(axis=0)
        total = np.where(window_valid, window_values, 0.0).sum(axis=0)
        result[day] = np.divide(total, count, out=np.full(volume.shape[1], np.nan), where=count > 0)
    return result


def _lot_round(value: float, lot: int) -> int:
    return 0 if not math.isfinite(float(value)) or value <= 0 else int(float(value) // lot) * lot


def _total_shares(lots: Mapping[str, list[dict[str, Any]]], asset: str) -> int:
    return sum(int(row["shares"]) for row in lots.get(asset, ()))


def _available_shares(lots: Mapping[str, list[dict[str, Any]]], asset: str, day: int) -> int:
    return sum(int(row["shares"]) for row in lots.get(asset, ()) if int(row["available_index"]) <= day)


def _consume(lots: dict[str, list[dict[str, Any]]], asset: str, shares: int, day: int) -> None:
    remaining = shares
    for lot in sorted(lots.get(asset, ()), key=lambda row: int(row["available_index"])):
        if int(lot["available_index"]) > day or remaining <= 0:
            continue
        used = min(int(lot["shares"]), remaining)
        lot["shares"] = int(lot["shares"]) - used
        remaining -= used
    lots[asset] = [row for row in lots.get(asset, ()) if int(row["shares"]) > 0]
    if remaining:
        raise IndependentCausalError("independent_sell_exceeded_available_shares")


def _position_value(lots: Mapping[str, list[dict[str, Any]]], assets: list[str], prices: np.ndarray) -> float:
    total = 0.0
    for index, asset in enumerate(assets):
        shares = _total_shares(lots, asset)
        if shares:
            price = float(prices[index])
            if not math.isfinite(price) or price <= 0:
                raise IndependentCausalError("independent_position_mark_invalid")
            total += shares * price
    return total
