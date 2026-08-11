"""A-share backtest contracts, portfolio accounting, costs, simulation, and command workflow."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class CostBreakdown:
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage: float
    market_impact: float
    total: float


@dataclass(frozen=True)
class AShareCostModel:
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0
    market_impact_bps: float = 0.0

    def estimate(self, side: str, trade_value: float) -> CostBreakdown:
        value = float(trade_value)
        if value <= 0 or not math.isfinite(value):
            return CostBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        commission = max(value * self.commission_rate, self.min_commission)
        stamp_duty = value * self.stamp_duty_rate if side.upper() == "SELL" else 0.0
        transfer_fee = value * self.transfer_fee_rate
        slippage = value * self.slippage_bps / 10000.0
        market_impact = value * self.market_impact_bps / 10000.0
        total = commission + stamp_duty + transfer_fee + slippage + market_impact
        return CostBreakdown(
            commission=float(commission),
            stamp_duty=float(stamp_duty),
            transfer_fee=float(transfer_fee),
            slippage=float(slippage),
            market_impact=float(market_impact),
            total=float(total),
        )

from typing import Any

import torch

from auto_alpha.research.factors.store import FactorValueRecord, LocalFactorStore


def select_factor_id(
    store: LocalFactorStore,
    factor_id: str | None = None,
    latest_approved: bool = False,
    factor_type: str = "any",
) -> str:
    if factor_id:
        return factor_id
    factors = store.load_factors()
    if factor_type not in {"single", "composite", "any"}:
        raise ValueError(f"unsupported factor_type: {factor_type}")
    if factor_type != "any":
        factors = [record for record in factors if _record_factor_type(record) == factor_type]
    if latest_approved:
        approved = [record for record in factors if record.status in {"approved", "production_candidate"}]
        if approved:
            return approved[-1].factor_id
        raise ValueError("no explicitly approved factor matches the requested factor type")
    if not factors:
        raise ValueError("factor store is empty; register a factor before running a portfolio simulation")
    return factors[-1].factor_id


def describe_factor(store: LocalFactorStore, factor_id: str) -> dict[str, Any]:
    for record in store.load_factors():
        if record.factor_id == factor_id:
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            component_ids = (
                record.parent_factor_ids
                or metadata.get("component_factor_ids")
                or []
            )
            return {
                "factor_id": record.factor_id,
                "factor_type": _record_factor_type(record),
                "component_factor_ids": list(component_ids) if isinstance(component_ids, list) else [],
                "status": record.status,
                "batch_id": record.batch_id,
            }
    return {
        "factor_id": factor_id,
        "factor_type": "unknown",
        "component_factor_ids": [],
        "status": "",
        "batch_id": None,
    }


def factor_values_to_matrix(
    records: list[FactorValueRecord],
    ts_codes: list[str],
    trade_dates: list[str],
    device: Any = None,
) -> torch.Tensor:
    code_index = {ts_code: idx for idx, ts_code in enumerate(ts_codes)}
    date_index = {trade_date: idx for idx, trade_date in enumerate(trade_dates)}
    matrix = torch.zeros((len(ts_codes), len(trade_dates)), dtype=torch.float32, device=device)
    for record in records:
        if record.ts_code not in code_index or record.trade_date not in date_index:
            continue
        matrix[code_index[record.ts_code], date_index[record.trade_date]] = (
            0.0 if record.value is None else float(record.value)
        )
    return matrix


def _record_factor_type(record) -> str:
    return record.factor_type or "single"

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class TargetPosition:
    trade_date: str
    ts_code: str
    target_weight: float
    factor_value: float | None = None
    optimized_weight: float | None = None
    benchmark_weight: float | None = None
    active_weight: float | None = None


@dataclass(frozen=True)
class TradeOrder:
    trade_date: str
    ts_code: str
    side: str
    target_weight: float
    current_weight: float
    order_value: float
    reason: str = "rebalance"


@dataclass(frozen=True)
class TradeFill:
    trade_date: str
    ts_code: str
    side: str
    price: float
    shares: int
    value: float
    cost: float
    status: str = "FILLED"
    allowed: bool = True
    reason: str = ""
    parent_order_id: str | None = None
    child_order_id: str | None = None
    bucket: str | None = None


@dataclass(frozen=True)
class PortfolioSnapshot:
    trade_date: str
    equity: float
    cash: float
    positions_value: float
    daily_return: float
    turnover: float
    cost: float
    n_positions: int


@dataclass(frozen=True)
class PortfolioBacktestResult:
    snapshots: list[PortfolioSnapshot]
    fills: list[TradeFill]
    metrics: dict[str, object]
    rebalance_audit: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshots": [asdict(snapshot) for snapshot in self.snapshots],
            "fills": [asdict(fill) for fill in self.fills],
            "rebalance_audit": self.rebalance_audit,
            "metrics": {
                key: float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
                for key, value in self.metrics.items()
            },
        }

from dataclasses import dataclass


@dataclass(frozen=True)
class AShareTradingRules:
    lot_size: int = 100
    max_position_weight: float = 0.10
    volume_limit_ratio: float = 0.10
    allow_fractional_weight: bool = True

    def round_shares(self, shares: float) -> int:
        if shares <= 0:
            return 0
        return int(shares // self.lot_size) * self.lot_size

    @staticmethod
    def is_t_plus_one_sell_allowed(buy_date_index: int, sell_date_index: int) -> bool:
        return sell_date_index > buy_date_index

    @staticmethod
    def can_buy(price: float, is_suspended: bool = False, is_limit_up: bool = False) -> tuple[bool, str]:
        if is_suspended:
            return False, "suspended"
        if is_limit_up:
            return False, "limit_up"
        if price <= 0:
            return False, "invalid_price"
        return True, ""

    @staticmethod
    def can_sell(price: float, is_suspended: bool = False, is_limit_down: bool = False) -> tuple[bool, str]:
        if is_suspended:
            return False, "suspended"
        if is_limit_down:
            return False, "limit_down"
        if price <= 0:
            return False, "invalid_price"
        return True, ""

    @staticmethod
    def is_open_at_limit(price: float, limit_price: float, *, direction: str) -> bool:
        if price <= 0 or limit_price <= 0:
            return False
        tolerance = max(abs(limit_price) * 1e-4, 1e-4)
        if direction == "up":
            return price >= limit_price - tolerance
        if direction == "down":
            return price <= limit_price + tolerance
        raise ValueError("direction must be up or down")

    def clamp_weight(self, weight: float) -> float:
        return max(0.0, min(float(weight), self.max_position_weight))

    def volume_limited_shares(self, requested_shares: int, volume: float) -> tuple[int, str]:
        max_shares = self.round_shares(max(float(volume), 0.0) * self.volume_limit_ratio)
        if max_shares <= 0:
            return 0, "volume_limit"
        if requested_shares > max_shares:
            return max_shares, "volume_limit_partial"
        return requested_shares, ""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class BacktestTimeContract:
    contract_id: str = "next_trade_day_open_v1"
    signal_time: str = "close_t"
    decision_time: str = "after_close_t"
    order_time: str = "before_open_t_plus_1"
    execution_time: str = "open_t_plus_1"
    price_field: str = "open"
    pnl_interval: str = "open_to_open"
    signal_lag_days: int = 1

    def validate(self) -> None:
        if self.signal_lag_days < 1:
            raise ValueError("next_trade_day_open requires signal_lag_days >= 1")
        if self.price_field != "open" or self.pnl_interval != "open_to_open":
            raise ValueError("formal daily next-open contract requires open fills and open-to-open PnL")

    def to_dict(self):
        self.validate()
        return asdict(self)


def normalize_execution_mode(value: str) -> tuple[str, list[str]]:
    mode = str(value)
    if mode == "next_trade_day_open":
        return mode, []
    if mode == "next_open":
        return "next_trade_day_open", ["next_open is a compatibility alias for next_trade_day_open"]
    raise ValueError(f"unsupported execution timing mode: {value}")

import math
from typing import Any

import torch



def _to_tensor(values: Any) -> torch.Tensor:
    if hasattr(values, "detach"):
        return values.detach().cpu()
    return torch.tensor(values, dtype=torch.float32)


def build_long_only_targets(
    factors,
    ts_codes: list[str],
    trade_dates: list[str],
    top_n: int = 20,
    max_weight: float = 0.10,
) -> list[list[TargetPosition]]:
    matrix = _to_tensor(factors).to(dtype=torch.float32)
    targets_by_date: list[list[TargetPosition]] = []
    for date_idx, trade_date in enumerate(trade_dates):
        values = matrix[:, date_idx]
        valid_indices = [idx for idx, value in enumerate(values.tolist()) if math.isfinite(float(value))]
        valid_indices.sort(key=lambda idx: float(values[idx].item()), reverse=True)
        selected = valid_indices[: max(0, top_n)]
        if not selected:
            targets_by_date.append([])
            continue
        weight = min(max_weight, 1.0 / len(selected))
        targets_by_date.append(
            [
                TargetPosition(
                    trade_date=trade_date,
                    ts_code=ts_codes[idx],
                    target_weight=float(weight),
                    factor_value=float(values[idx].item()),
                )
                for idx in selected
            ]
        )
    return targets_by_date


def targets_to_weight_matrix(
    targets_by_date: list[list[TargetPosition]],
    ts_codes: list[str],
    trade_dates: list[str],
) -> torch.Tensor:
    code_index = {ts_code: idx for idx, ts_code in enumerate(ts_codes)}
    date_index = {trade_date: idx for idx, trade_date in enumerate(trade_dates)}
    weights = torch.zeros((len(ts_codes), len(trade_dates)), dtype=torch.float32)
    for targets in targets_by_date:
        for target in targets:
            if target.ts_code in code_index and target.trade_date in date_index:
                weights[code_index[target.ts_code], date_index[target.trade_date]] = float(target.target_weight)
    return weights

import math

import torch

from auto_alpha.portfolio.simulator.capacity import CapacityConfig, build_capacity_report
from auto_alpha.execution.trading.engine import ExecutionOrder
from auto_alpha.execution.trading.plan import ExecutionPlanConfig, ExecutionPlanResult, build_execution_schedule, build_parent_orders_from_target_orders, simulate_child_orders
from auto_alpha.portfolio.construction.optimizer import OptimizationConfig, PortfolioOptimizer
from auto_alpha.portfolio.risk.model import (
    active_risk_decomposition,
    attribute_active_return,
    benchmark_weights_from_index_members,
    build_barra_like_risk_model,
    build_risk_report,
    estimate_return_covariance,
    portfolio_factor_exposure,
    portfolio_risk_decomposition,
)



class AShareBacktestSimulator:
    def __init__(
        self,
        initial_cash: float = 1_000_000.0,
        top_n: int = 20,
        max_weight: float = 0.10,
        portfolio_method: str = "equal_weight",
        index_code: str = "000300.SH",
        risk_aversion: float = 1.0,
        turnover_penalty: float = 0.1,
        max_turnover: float = 1.0,
        max_industry_active_weight: float = 0.20,
        max_tracking_error: float = 1.0,
        factor_id: str | None = None,
        use_factor_risk_model: bool = False,
        risk_model_lookback: int | None = None,
        risk_model_shrinkage: float = 0.1,
        attribution: bool = False,
        max_style_exposure: float | None = None,
        max_active_style_exposure: float | None = None,
        max_factor_risk_contribution: float | None = None,
        capacity_aware: bool = False,
        capacity_lookback: int = 20,
        max_participation: float = 0.10,
        impact_base_bps: float = 5.0,
        impact_power: float = 0.5,
        execution_buckets: tuple[str, ...] | None = None,
        time_contract: BacktestTimeContract | None = None,
        cost_model: AShareCostModel | None = None,
        trading_rules: AShareTradingRules | None = None,
    ):
        self.initial_cash = float(initial_cash)
        self.top_n = int(top_n)
        self.max_weight = float(max_weight)
        self.portfolio_method = portfolio_method
        self.index_code = index_code
        self.factor_id = factor_id
        self.optimization_config = OptimizationConfig(
            risk_aversion=risk_aversion,
            turnover_penalty=turnover_penalty,
            max_weight=max_weight,
            max_names=top_n,
            max_turnover=max_turnover,
            max_industry_active_weight=max_industry_active_weight,
            max_tracking_error=max_tracking_error,
            use_factor_risk_model=use_factor_risk_model,
            risk_model_lookback=risk_model_lookback,
            risk_model_shrinkage=risk_model_shrinkage,
            max_style_exposure=max_style_exposure,
            max_active_style_exposure=max_active_style_exposure,
            max_factor_risk_contribution=max_factor_risk_contribution,
        )
        self.use_factor_risk_model = bool(use_factor_risk_model)
        self.risk_model_lookback = risk_model_lookback
        self.risk_model_shrinkage = float(risk_model_shrinkage)
        self.attribution = bool(attribution)
        self.capacity_aware = bool(capacity_aware)
        self.capacity_config = CapacityConfig(
            lookback=capacity_lookback,
            max_participation=max_participation,
            impact_base_bps=impact_base_bps,
            impact_power=impact_power,
        )
        self.time_contract = time_contract or BacktestTimeContract()
        self.time_contract.validate()
        buckets = execution_buckets or ("open",)
        if tuple(buckets) != ("open",):
            raise ValueError("formal daily next-open backtest only supports the open bucket")
        self.execution_plan_config = ExecutionPlanConfig(
            buckets=buckets,
            max_child_participation=max_participation,
            capacity_lookback=capacity_lookback,
            impact_base_bps=impact_base_bps,
            impact_power=impact_power,
            price_field="open",
        )
        self.cost_model = cost_model or AShareCostModel()
        self.trading_rules = trading_rules or AShareTradingRules(max_position_weight=max_weight)
        self.risk_reports: list[object] = []
        self.optimization_results: list[object] = []
        self.risk_exposure_rows: list[dict[str, object]] = []
        self.risk_decomposition_rows: list[dict[str, object]] = []
        self.return_attribution_rows: list[dict[str, object]] = []
        self.capacity_reports: list[object] = []
        self.execution_plan_results: list[ExecutionPlanResult] = []

    def simulate(self, factors, loader) -> PortfolioBacktestResult:
        if self.portfolio_method not in {"equal_weight", "risk_aware"}:
            raise ValueError("portfolio_method must be equal_weight or risk_aware")
        factor_tensor = factors.detach().cpu() if hasattr(factors, "detach") else torch.tensor(factors)
        open_price = loader.raw_data_cache["open"].detach().cpu()
        volume = loader.raw_data_cache.get("volume", torch.zeros_like(open_price)).detach().cpu()
        is_suspended = loader.raw_data_cache.get("is_suspended", torch.zeros_like(open_price)).detach().cpu()
        open_at_up_limit = _open_limit_matrix(loader.raw_data_cache, open_price, "up")
        open_at_down_limit = _open_limit_matrix(loader.raw_data_cache, open_price, "down")
        active_mask = loader.raw_data_cache.get("active_mask", torch.ones_like(open_price)).detach().cpu()
        target_ret = loader.target_ret.detach().cpu()
        if self.portfolio_method == "equal_weight":
            targets_by_date = build_long_only_targets(
                factor_tensor,
                loader.ts_codes,
                loader.trade_dates,
                top_n=self.top_n,
                max_weight=self.max_weight,
            )
            target_weights = targets_to_weight_matrix(targets_by_date, loader.ts_codes, loader.trade_dates)
        else:
            target_weights = None

        current_weights = torch.zeros(len(loader.ts_codes), dtype=torch.float32)
        first_buy_index: dict[int, int] = {}
        equity = self.initial_cash
        prev_equity = self.initial_cash
        snapshots: list[PortfolioSnapshot] = []
        fills: list[TradeFill] = []
        rebalance_audit: list[dict[str, object]] = []
        total_cost = 0.0
        optimizer = PortfolioOptimizer(self.optimization_config)
        risk_metric_rows: list[dict[str, float]] = []
        factor_risk_model = None

        for date_idx, trade_date in enumerate(loader.trade_dates):
            realized_return = 0.0
            open_to_open = torch.zeros(len(loader.ts_codes), dtype=torch.float32)
            risk_as_of_index = max(0, date_idx - 1)
            covariance = estimate_return_covariance(loader, lookback=self.risk_model_lookback, shrinkage=self.risk_model_shrinkage, as_of_index=risk_as_of_index)
            factor_risk_model = None
            if self.use_factor_risk_model and date_idx > 0:
                factor_risk_model = build_barra_like_risk_model(
                    loader,
                    lookback=self.risk_model_lookback,
                    shrinkage=self.risk_model_shrinkage,
                    as_of_index=risk_as_of_index,
                )
            if date_idx > 0:
                prior_open = open_price[:, date_idx - 1]
                current_open = open_price[:, date_idx]
                valid_open = (
                    torch.isfinite(prior_open)
                    & torch.isfinite(current_open)
                    & (prior_open > 0)
                    & (current_open > 0)
                )
                open_to_open = torch.where(valid_open, current_open / prior_open - 1.0, torch.zeros_like(current_open))
                realized_return = float((current_weights * open_to_open).sum().item())
                equity *= 1.0 + realized_return
                current_weights = _drift_weights(current_weights, open_to_open, realized_return)

            pre_trade_weights = current_weights.clone()

            if self.portfolio_method == "risk_aware":
                benchmark = benchmark_weights_from_index_members(loader, self.index_code, trade_date)
                if self.use_factor_risk_model and factor_risk_model is None:
                    desired_weights = current_weights.clone()
                    rebalance_audit.append(
                        _rebalance_audit_row(
                            trade_date,
                            loader.ts_codes,
                            pre_trade_weights,
                            desired_weights,
                            current_weights,
                        )
                    )
                    snapshots.append(
                        PortfolioSnapshot(
                            trade_date=trade_date,
                            equity=equity,
                            cash=equity,
                            positions_value=0.0,
                            daily_return=realized_return if date_idx > 0 else 0.0,
                            turnover=0.0,
                            cost=0.0,
                            n_positions=int(torch.count_nonzero(current_weights).item()),
                        )
                    )
                    continue
                opt_result = optimizer.optimize(
                    factor_tensor[:, date_idx],
                    current_weights=current_weights,
                    benchmark_weights=benchmark,
                    covariance=covariance,
                    loader=loader,
                    factor_risk_model=factor_risk_model,
                    date_index=risk_as_of_index if factor_risk_model is not None else None,
                )
                self.optimization_results.append(opt_result)
                desired_weights = torch.tensor(
                    [opt_result.weights.get(ts_code, 0.0) for ts_code in loader.ts_codes],
                    dtype=torch.float32,
                )
                risk_report = build_risk_report(
                    desired_weights,
                    benchmark,
                    loader,
                    self.index_code,
                    trade_date,
                    factor_id=self.factor_id,
                    covariance=covariance,
                    turnover=opt_result.turnover,
                    factor_risk_model=factor_risk_model,
                )
                self.risk_reports.append(risk_report)
                risk_metric_rows.append(risk_report.metrics.to_dict())
                if factor_risk_model is not None:
                    style_names = set(factor_risk_model.exposure_matrix.style_factor_names)
                    exposure = portfolio_factor_exposure(desired_weights, factor_risk_model, date_idx)
                    active_style = portfolio_factor_exposure(desired_weights - benchmark, factor_risk_model, date_idx)
                    risk_payload = portfolio_risk_decomposition(desired_weights, factor_risk_model, date_idx)
                    active_payload = active_risk_decomposition(desired_weights, benchmark, factor_risk_model, date_idx)
                    factor_return_count = int(factor_risk_model.factor_returns.returns.shape[1])
                    attribution_payload = (
                        attribute_active_return(
                            desired_weights,
                            benchmark,
                            open_to_open,
                            factor_risk_model.exposure_matrix,
                            factor_risk_model.factor_returns,
                            risk_as_of_index,
                        )
                        if self.attribution and factor_return_count > 0 and date_idx > 0
                        else {}
                    )
                    self.risk_exposure_rows.append(
                        {
                            "trade_date": trade_date,
                            "factor_id": self.factor_id,
                            "style_exposures": {name: float(exposure.get(name, 0.0)) for name in sorted(style_names)},
                            "active_style_exposures": {name: float(active_style.get(name, 0.0)) for name in sorted(style_names)},
                            "max_style_exposure_abs": max((abs(float(exposure.get(name, 0.0))) for name in style_names), default=0.0),
                            "max_active_style_exposure_abs": max((abs(float(active_style.get(name, 0.0))) for name in style_names), default=0.0),
                        }
                    )
                    self.risk_decomposition_rows.append(
                        {
                            "trade_date": trade_date,
                            "factor_id": self.factor_id,
                            "portfolio": risk_payload,
                            "active": active_payload,
                        }
                    )
                    if attribution_payload:
                        self.return_attribution_rows.append({"trade_date": trade_date, "factor_id": self.factor_id, **attribution_payload})
            else:
                desired_weights = target_weights[:, date_idx].clone()
            desired_weights = torch.clamp(desired_weights, 0.0, self.max_weight)
            desired_weights = desired_weights * active_mask[:, date_idx]
            deltas = desired_weights - current_weights
            day_cost = 0.0
            day_traded_value = 0.0
            adjusted_weights = current_weights.clone()

            if self.capacity_aware:
                adjusted_weights, day_cost, day_traded_value, day_fills = self._execute_capacity_aware_day(
                    deltas,
                    current_weights,
                    desired_weights,
                    equity,
                    loader,
                    trade_date,
                    date_idx,
                    first_buy_index,
                )
                fills.extend(day_fills)
                equity = max(equity - day_cost, 0.0)
                total_cost += day_cost
                current_weights = adjusted_weights
                turnover = float(torch.abs(current_weights - pre_trade_weights).sum().item())
                rebalance_audit.append(
                    _rebalance_audit_row(
                        trade_date,
                        loader.ts_codes,
                        pre_trade_weights,
                        desired_weights,
                        current_weights,
                    )
                )
                invested_weight = float(current_weights.sum().item())
                positions_value = equity * invested_weight
                cash = equity - positions_value
                daily_return = (equity / prev_equity - 1.0) if prev_equity > 0 else 0.0
                snapshots.append(
                    PortfolioSnapshot(
                        trade_date=trade_date,
                        equity=float(equity),
                        cash=float(cash),
                        positions_value=float(positions_value),
                        daily_return=float(daily_return),
                        turnover=float(turnover),
                        cost=float(day_cost),
                        n_positions=int((current_weights > 0).sum().item()),
                    )
                )
                prev_equity = equity
                continue

            for stock_idx, delta in enumerate(deltas.tolist()):
                if abs(delta) <= 1e-9:
                    continue
                side = "BUY" if delta > 0 else "SELL"
                price = float(open_price[stock_idx, date_idx].item())
                active = bool(active_mask[stock_idx, date_idx].item() > 0.5)
                suspended = bool(is_suspended[stock_idx, date_idx].item() > 0.5)
                is_limit_up = bool(open_at_up_limit[stock_idx, date_idx].item() > 0.5)
                is_limit_down = bool(open_at_down_limit[stock_idx, date_idx].item() > 0.5)
                if not active:
                    allowed, reason = False, "inactive_security"
                elif side == "BUY":
                    allowed, reason = self.trading_rules.can_buy(price, suspended, is_limit_up)
                else:
                    allowed, reason = self.trading_rules.can_sell(price, suspended, is_limit_down)
                    buy_index = first_buy_index.get(stock_idx, -1)
                    if allowed and buy_index >= 0 and not self.trading_rules.is_t_plus_one_sell_allowed(
                        buy_index, date_idx
                    ):
                        allowed, reason = False, "t_plus_one"
                order_value = abs(delta) * equity
                requested_shares = self.trading_rules.round_shares(order_value / price) if allowed and price > 0 else 0
                shares, volume_reason = self.trading_rules.volume_limited_shares(
                    requested_shares,
                    float(volume[stock_idx, date_idx].item()),
                ) if requested_shares > 0 else (0, "")
                status = "FILLED"
                if not allowed:
                    status = "REJECTED"
                    shares = 0
                elif requested_shares <= 0:
                    status = "REJECTED"
                    reason = "zero_shares"
                elif shares <= 0:
                    status = "REJECTED"
                    reason = volume_reason or "zero_shares"
                elif shares < requested_shares:
                    status = "PARTIAL"
                    reason = volume_reason or "partial_fill"
                if shares <= 0:
                    fills.append(
                        TradeFill(
                            trade_date=trade_date,
                            ts_code=loader.ts_codes[stock_idx],
                            side=side,
                            price=float(price),
                            shares=0,
                            value=0.0,
                            cost=0.0,
                            status=status,
                            allowed=False,
                            reason=reason,
                        )
                    )
                    continue
                fill_value = shares * price
                cost = self.cost_model.estimate(side, fill_value).total
                day_cost += cost
                day_traded_value += fill_value
                fills.append(
                    TradeFill(
                        trade_date=trade_date,
                        ts_code=loader.ts_codes[stock_idx],
                        side=side,
                        price=float(price),
                        shares=int(shares),
                        value=float(fill_value),
                        cost=float(cost),
                        status=status,
                        allowed=allowed,
                        reason=reason,
                    )
                )
                filled_weight = fill_value / max(equity, 1e-6)
                if side == "BUY":
                    adjusted_weights[stock_idx] = min(desired_weights[stock_idx], current_weights[stock_idx] + filled_weight)
                else:
                    adjusted_weights[stock_idx] = max(desired_weights[stock_idx], current_weights[stock_idx] - filled_weight)
                if side == "BUY":
                    first_buy_index.setdefault(stock_idx, date_idx)

            equity = max(equity - day_cost, 0.0)
            total_cost += day_cost
            current_weights = adjusted_weights
            turnover = float(torch.abs(current_weights - pre_trade_weights).sum().item())
            rebalance_audit.append(
                _rebalance_audit_row(
                    trade_date,
                    loader.ts_codes,
                    pre_trade_weights,
                    desired_weights,
                    current_weights,
                )
            )
            invested_weight = float(current_weights.sum().item())
            positions_value = equity * invested_weight
            cash = equity - positions_value
            daily_return = (equity / prev_equity - 1.0) if prev_equity > 0 else 0.0
            snapshots.append(
                PortfolioSnapshot(
                    trade_date=trade_date,
                    equity=float(equity),
                    cash=float(cash),
                    positions_value=float(positions_value),
                    daily_return=float(daily_return),
                    turnover=float(turnover),
                    cost=float(day_cost),
                    n_positions=int((current_weights > 0).sum().item()),
                )
            )
            prev_equity = equity

        metrics = self._metrics(snapshots, fills, total_cost)
        filled = [fill for fill in fills if fill.status in {"FILLED", "PARTIAL"}]
        fill_audit_ok = all(
            abs(fill.price - float(open_price[loader.ts_codes.index(fill.ts_code), loader.trade_dates.index(fill.trade_date)].item())) <= 1e-8
            for fill in filled
        )
        metrics["signal_contract_next_open"] = 1.0 if fill_audit_ok else 0.0
        metrics["time_contract"] = self.time_contract.to_dict()
        if risk_metric_rows:
            metrics.update(_average_risk_metrics(risk_metric_rows))
        if self.risk_decomposition_rows:
            metrics.update(_average_factor_risk_metrics(self.risk_decomposition_rows, self.risk_exposure_rows))
        if self.execution_plan_results:
            metrics.update(_average_execution_quality(self.execution_plan_results))
        return PortfolioBacktestResult(
            snapshots=snapshots,
            fills=fills,
            metrics=metrics,
            rebalance_audit=rebalance_audit,
        )

    def _execute_capacity_aware_day(
        self,
        deltas: torch.Tensor,
        current_weights: torch.Tensor,
        desired_weights: torch.Tensor,
        equity: float,
        loader,
        trade_date: str,
        date_idx: int,
        first_buy_index: dict[int, int],
    ) -> tuple[torch.Tensor, float, float, list[TradeFill]]:
        orders: list[ExecutionOrder] = []
        for stock_idx, delta in enumerate(deltas.tolist()):
            if abs(delta) <= 1e-9:
                continue
            side = "BUY" if delta > 0 else "SELL"
            active_mask = loader.raw_data_cache.get("active_mask")
            if active_mask is not None and float(active_mask.detach().cpu()[stock_idx, date_idx].item()) <= 0.5:
                fills = [
                    TradeFill(
                        trade_date=trade_date,
                        ts_code=loader.ts_codes[stock_idx],
                        side=side,
                        price=0.0,
                        shares=0,
                        value=0.0,
                        cost=0.0,
                        status="REJECTED",
                        allowed=False,
                        reason="inactive_security",
                    )
                ]
                return current_weights.clone(), 0.0, 0.0, fills
            if side == "SELL":
                buy_index = first_buy_index.get(stock_idx, -1)
                if buy_index >= 0 and not self.trading_rules.is_t_plus_one_sell_allowed(buy_index, date_idx):
                    orders.append(
                        ExecutionOrder(
                            trade_date=trade_date,
                            ts_code=loader.ts_codes[stock_idx],
                            side=side,
                            target_weight=float(desired_weights[stock_idx].item()),
                            order_value=abs(float(delta)) * equity,
                            reason="t_plus_one",
                        )
                    )
                    continue
            orders.append(
                ExecutionOrder(
                    trade_date=trade_date,
                    ts_code=loader.ts_codes[stock_idx],
                    side=side,
                    target_weight=float(desired_weights[stock_idx].item()),
                    order_value=abs(float(delta)) * equity,
                    reason="rebalance",
                )
            )
        if not orders:
            return current_weights.clone(), 0.0, 0.0, []
        parents = build_parent_orders_from_target_orders(orders)
        schedule, capacity = build_execution_schedule(parents, loader, trade_date, self.execution_plan_config)
        simulated = simulate_child_orders(schedule, loader, self.cost_model, self.trading_rules)
        capacity_report = build_capacity_report(capacity, self.capacity_config, {"source": "backtest"})
        result = ExecutionPlanResult(
            schedule=schedule,
            fills=simulated.fills,
            quality=simulated.quality,
            capacity_report=capacity_report.to_dict(),
        )
        self.capacity_reports.append(capacity_report)
        self.execution_plan_results.append(result)
        adjusted_weights = current_weights.clone()
        day_cost = 0.0
        day_traded_value = 0.0
        trade_fills: list[TradeFill] = []
        for fill in simulated.fills:
            stock_idx = loader.ts_codes.index(fill.ts_code)
            if fill.status in {"FILLED", "PARTIAL"} and fill.shares > 0:
                filled_weight = float(fill.value) / max(equity, 1e-6)
                if fill.side == "BUY":
                    adjusted_weights[stock_idx] = min(desired_weights[stock_idx], adjusted_weights[stock_idx] + filled_weight)
                    first_buy_index.setdefault(stock_idx, date_idx)
                else:
                    adjusted_weights[stock_idx] = max(desired_weights[stock_idx], adjusted_weights[stock_idx] - filled_weight)
                day_cost += float(fill.cost)
                day_traded_value += float(fill.value)
            trade_fills.append(
                TradeFill(
                    trade_date=fill.trade_date,
                    ts_code=fill.ts_code,
                    side=fill.side,
                    price=fill.price,
                    shares=fill.shares,
                    value=fill.value,
                    cost=fill.cost,
                    status=fill.status,
                    allowed=fill.status != "REJECTED",
                    reason=fill.reason,
                    parent_order_id=fill.parent_order_id,
                    child_order_id=fill.child_order_id,
                    bucket=fill.bucket,
                )
            )
        return adjusted_weights, float(day_cost), float(day_traded_value), trade_fills

    def _metrics(self, snapshots: list[PortfolioSnapshot], fills: list[TradeFill], total_cost: float) -> dict[str, float]:
        if not snapshots:
            return {
                "total_return": 0.0,
                "annualized_return": 0.0,
                "sharpe": 0.0,
                "max_drawdown": 0.0,
                "avg_turnover": 0.0,
                "total_cost": 0.0,
                "n_trades": 0.0,
                "rejected_trades": 0.0,
                "partial_fills": 0.0,
                "fill_rate": 0.0,
                "constraint_reject_rate": 0.0,
                "avg_exposure": 0.0,
                "cash_drag": 0.0,
            }
        returns = torch.tensor([snapshot.daily_return for snapshot in snapshots], dtype=torch.float32)
        final_equity = snapshots[-1].equity
        total_return = final_equity / self.initial_cash - 1.0 if self.initial_cash > 0 else 0.0
        annualized_return = (1.0 + total_return) ** (252.0 / max(len(snapshots), 1)) - 1.0
        std = float(returns.std(unbiased=False).item())
        sharpe = float(returns.mean().item() / (std + 1e-6) * math.sqrt(252.0)) if len(snapshots) > 1 else 0.0
        equity_curve = torch.tensor([snapshot.equity for snapshot in snapshots], dtype=torch.float32)
        running_max = torch.cummax(equity_curve, dim=0).values
        drawdowns = 1.0 - equity_curve / torch.clamp(running_max, min=1e-6)
        rejected = sum(1 for fill in fills if fill.status == "REJECTED")
        partial = sum(1 for fill in fills if fill.status == "PARTIAL")
        completed = sum(1 for fill in fills if fill.status in {"FILLED", "PARTIAL"})
        fill_rate = completed / len(fills) if fills else 0.0
        constraint_rejects = sum(
            1
            for fill in fills
            if fill.status == "REJECTED" and fill.reason in {"suspended", "limit_up", "limit_down", "t_plus_one", "volume_limit", "inactive_security"}
        )
        inactive = sum(1 for fill in fills if fill.reason == "inactive_security")
        exposure_values = [
            snapshot.positions_value / snapshot.equity if snapshot.equity > 0 else 0.0
            for snapshot in snapshots
        ]
        avg_exposure = sum(exposure_values) / len(exposure_values) if exposure_values else 0.0
        return {
            "total_return": float(total_return),
            "annualized_return": float(annualized_return),
            "sharpe": float(sharpe),
            "max_drawdown": float(drawdowns.max().item()),
            "avg_turnover": float(sum(snapshot.turnover for snapshot in snapshots) / len(snapshots)),
            "total_cost": float(total_cost),
            "n_trades": float(len(fills)),
            "rejected_trades": float(rejected),
            "partial_fills": float(partial),
            "fill_rate": float(fill_rate),
            "constraint_reject_rate": float(constraint_rejects / len(fills) if fills else 0.0),
            "avg_exposure": float(avg_exposure),
            "cash_drag": float(1.0 - avg_exposure),
            "inactive_security_order_count": float(inactive),
            "inactive_security_order_value": float(sum(fill.value for fill in fills if fill.reason == "inactive_security")),
            "pit_filtered_security_count": float(inactive),
            "active_universe_coverage": 1.0,
            "signal_lag_days": 0.0,
            "leakage_warning_count": 0.0,
            "leakage_blocker_count": 0.0,
        }


def _average_risk_metrics(rows: list[dict[str, float]]) -> dict[str, float]:
    def avg(key: str) -> float:
        values = [float(row.get(key, 0.0) or 0.0) for row in rows]
        return sum(values) / len(values) if values else 0.0

    return {
        "tracking_error": avg("tracking_error"),
        "avg_active_share": avg("active_share"),
        "avg_hhi": avg("hhi"),
        "avg_top_weight": avg("top_weight"),
        "avg_industry_active": avg("industry_active_max"),
        "risk_constraint_violations": avg("violations"),
    }


def _average_factor_risk_metrics(
    decomposition_rows: list[dict[str, object]],
    exposure_rows: list[dict[str, object]],
) -> dict[str, float]:
    portfolio_rows = [row.get("portfolio", {}) for row in decomposition_rows]
    active_rows = [row.get("active", {}) for row in decomposition_rows]

    def avg(rows, key: str) -> float:
        values = [float(row.get(key, 0.0) or 0.0) for row in rows if isinstance(row, dict)]
        return sum(values) / len(values) if values else 0.0

    return {
        "avg_factor_risk": avg(portfolio_rows, "factor_risk"),
        "avg_specific_risk": avg(portfolio_rows, "specific_risk"),
        "avg_active_factor_risk": avg(active_rows, "factor_risk"),
        "max_style_exposure_abs": max((float(row.get("max_style_exposure_abs", 0.0) or 0.0) for row in exposure_rows), default=0.0),
        "max_active_style_exposure_abs": max((float(row.get("max_active_style_exposure_abs", 0.0) or 0.0) for row in exposure_rows), default=0.0),
        "max_factor_risk_share": max((float(row.get("factor_risk_share", 0.0) or 0.0) for row in portfolio_rows if isinstance(row, dict)), default=0.0),
        "max_specific_risk_share": max((float(row.get("specific_risk_share", 0.0) or 0.0) for row in portfolio_rows if isinstance(row, dict)), default=0.0),
    }


def _average_execution_quality(results: list[ExecutionPlanResult]) -> dict[str, float]:
    qualities = [result.quality for result in results]

    def avg(attr: str) -> float:
        values = [float(getattr(item, attr, 0.0) or 0.0) for item in qualities]
        return sum(values) / len(values) if values else 0.0

    return {
        "avg_amount_participation": _avg_capacity(results, "amount_participation"),
        "avg_volume_participation": _avg_capacity(results, "volume_participation"),
        "estimated_impact_cost": sum(float(item.quality.estimated_impact_cost) for item in results),
        "realized_execution_cost": sum(float(item.quality.realized_execution_cost) for item in results),
        "unfilled_order_value": sum(float(item.quality.unfilled_order_value) for item in results),
        "execution_fill_rate": avg("execution_fill_rate"),
        "capacity_warning_count": float(sum(int((result.capacity_report.get("portfolio", {}) or {}).get("capacity_warning_count", 0)) for result in results)),
    }


def _avg_capacity(results: list[ExecutionPlanResult], key: str) -> float:
    values = []
    for result in results:
        portfolio = result.capacity_report.get("portfolio", {}) if isinstance(result.capacity_report, dict) else {}
        for record in portfolio.get("records", []) if isinstance(portfolio.get("records"), list) else []:
            if isinstance(record, dict):
                values.append(float(record.get(key, 0.0) or 0.0))
    return sum(values) / len(values) if values else 0.0


def _open_limit_matrix(
    raw_data_cache: dict[str, torch.Tensor],
    open_price: torch.Tensor,
    direction: str,
) -> torch.Tensor:
    explicit_name = "open_at_up_limit" if direction == "up" else "open_at_down_limit"
    explicit = raw_data_cache.get(explicit_name)
    if explicit is not None:
        return explicit.detach().cpu().to(dtype=torch.float32)
    limit_name = "up_limit" if direction == "up" else "down_limit"
    limit = raw_data_cache.get(limit_name)
    if limit is None:
        return torch.zeros_like(open_price)
    limit = limit.detach().cpu()
    valid = torch.isfinite(open_price) & torch.isfinite(limit) & (open_price > 0) & (limit > 0)
    tolerance = torch.clamp(limit.abs() * 1e-4, min=1e-4)
    if direction == "up":
        result = valid & (open_price >= limit - tolerance)
    else:
        result = valid & (open_price <= limit + tolerance)
    return result.to(dtype=torch.float32)


def _drift_weights(
    current_weights: torch.Tensor,
    asset_returns: torch.Tensor,
    portfolio_return: float,
) -> torch.Tensor:
    gross_return = 1.0 + float(portfolio_return)
    if gross_return <= 0:
        raise ValueError("portfolio gross return must remain positive")
    drifted = current_weights * (1.0 + asset_returns) / gross_return
    return torch.where(torch.isfinite(drifted), drifted, torch.zeros_like(drifted))


def _rebalance_audit_row(
    trade_date: str,
    ts_codes: list[str],
    pre_trade_weights: torch.Tensor,
    target_weights: torch.Tensor,
    post_trade_weights: torch.Tensor,
) -> dict[str, object]:
    def sparse(values: torch.Tensor) -> dict[str, float]:
        return {
            ts_code: float(values[index].item())
            for index, ts_code in enumerate(ts_codes)
            if abs(float(values[index].item())) > 1e-12
        }

    turnover = float(torch.abs(post_trade_weights - pre_trade_weights).sum().item())
    return {
        "trade_date": trade_date,
        "pre_trade_weights": sparse(pre_trade_weights),
        "target_weights": sparse(target_weights),
        "post_trade_weights": sparse(post_trade_weights),
        "turnover": turnover,
        "turnover_contract": "l1_post_trade_minus_drifted_pre_trade",
    }

import argparse
import contextlib
import io
import json
from dataclasses import asdict
from pathlib import Path

from auto_alpha.data.pit.corporate_actions.models import CorporateActionEvent
from auto_alpha.data.pit.corporate_actions.normalizer import normalize_corporate_action_records
from auto_alpha.data.pit.corporate_actions.report import read_jsonl, write_corporate_action_report
from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.research.formulas.data_loader import AShareDataLoader
from auto_alpha.portfolio.simulator.capacity import write_capacity_report
from auto_alpha.execution.trading.plan import write_execution_plan_report
from auto_alpha.portfolio.construction.optimizer import load_portfolio_policy, portfolio_policy_from_payload, validate_certified_portfolio_policy
from auto_alpha.portfolio.risk.model import write_risk_model_report, write_risk_report
from auto_alpha.portfolio.risk.controls import evaluate_order_records
from auto_alpha.data.lake.store import validate_research_input
from auto_alpha.validation.walk_forward.engine_report import write_stress_backtest_artifacts
from auto_alpha.validation.walk_forward.engine_stress_backtest import run_stress_backtest_bundle



def _write_jsonl(path: Path, records: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _write_dict_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local A-share portfolio simulation.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-freeze-id")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--freeze-validation-report-path")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--factor-id")
    parser.add_argument("--latest-approved", action="store_true")
    parser.add_argument("--factor-type", choices=["single", "composite", "any"], default="any")
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--max-weight", type=float, default=0.10)
    parser.add_argument("--portfolio-method", choices=["equal_weight", "risk_aware"], default="equal_weight")
    parser.add_argument("--portfolio-policy-path")
    parser.add_argument("--portfolio-policy-id")
    parser.add_argument("--require-certified-portfolio-policy", action="store_true")
    parser.add_argument("--portfolio-certification-decision-path")
    parser.add_argument("--active-optimizer-policy", action="store_true")
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--risk-aversion", type=float, default=1.0)
    parser.add_argument("--turnover-penalty", type=float, default=0.1)
    parser.add_argument("--max-turnover", type=float, default=1.0)
    parser.add_argument("--max-industry-active-weight", type=float, default=0.20)
    parser.add_argument("--max-tracking-error", type=float, default=1.0)
    parser.add_argument("--risk-report-dir")
    parser.add_argument("--use-factor-risk-model", action="store_true")
    parser.add_argument("--risk-model-lookback", type=int)
    parser.add_argument("--risk-model-shrinkage", type=float, default=0.1)
    parser.add_argument("--attribution", action="store_true")
    parser.add_argument("--max-style-exposure", type=float)
    parser.add_argument("--max-active-style-exposure", type=float)
    parser.add_argument("--max-factor-risk-contribution", type=float)
    parser.add_argument("--capacity-aware", action="store_true")
    parser.add_argument("--capacity-lookback", type=int, default=20)
    parser.add_argument("--max-participation", type=float, default=0.10)
    parser.add_argument("--impact-base-bps", type=float, default=5.0)
    parser.add_argument("--impact-power", type=float, default=0.5)
    parser.add_argument("--execution-buckets", default="open")
    parser.add_argument("--execution-plan-dir")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="next_trade_day_open")
    parser.add_argument("--signal-lag-days", type=int, default=1)
    parser.add_argument("--min-listing-days", type=int, default=0)
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--active-mask-path")
    parser.add_argument("--run-leakage-audit", action="store_true")
    parser.add_argument("--leakage-audit-dir")
    parser.add_argument("--fail-on-leakage-blocker", action="store_true")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument("--corporate-action-dir")
    parser.add_argument(
        "--target-return-mode",
        choices=["adjusted_close", "raw_close", "corporate_action_total_return"],
        default="adjusted_close",
    )
    parser.add_argument("--apply-corporate-actions", action="store_true")
    parser.add_argument("--corporate-action-application-date-mode", default="pay_date")
    parser.add_argument("--corporate-action-report-dir")
    parser.add_argument("--corporate-action-cash-field", default="cash_div")
    parser.add_argument("--reconcile-adjustment-factors", action="store_true")
    parser.add_argument("--settlement-aware", action="store_true")
    parser.add_argument("--settlement-dir")
    parser.add_argument(
        "--settlement-profile",
        choices=["cn_ashare_paper_default", "conservative_t_plus_one_cash", "immediate_legacy"],
        default="cn_ashare_paper_default",
    )
    parser.add_argument("--cost-basis-method", choices=["average", "fifo"], default="average")
    parser.add_argument("--enforce-available-cash", action="store_true")
    parser.add_argument("--enforce-available-shares", action="store_true")
    parser.add_argument("--allow-unsettled-cash-for-buy", action="store_true")
    parser.add_argument("--allow-unsettled-shares-for-sell", action="store_true")
    parser.add_argument("--settle-through-date")
    parser.add_argument("--write-settlement-report", action="store_true")
    parser.add_argument("--risk-controls", action="store_true")
    parser.add_argument("--risk-policy-path")
    parser.add_argument("--risk-policy-profile", default="cn_ashare_paper_default")
    parser.add_argument("--risk-control-dir")
    parser.add_argument("--risk-fail-on-breach", action="store_true")
    parser.add_argument("--risk-allow-clipping", action="store_true")
    parser.add_argument("--risk-state-reset-each-run", action="store_true")
    parser.add_argument("--validation-bundle", action="store_true")
    parser.add_argument("--validation-output-dir")
    parser.add_argument("--stress-cost-multipliers", default="1.0,2.0")
    parser.add_argument("--stress-participations", default="0.10,0.05")
    parser.add_argument("--stress-settlement-profiles", default="cn_ashare_paper_default,conservative_t_plus_one_cash")
    parser.add_argument("--stress-top-n-values", default="")
    parser.add_argument("--stress-max-weight-values", default="")
    parser.add_argument("--write-validation-stress-report", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.feature_cutoff_mode == "same_day_after_close" and int(args.signal_lag_days) <= 0:
        print(json.dumps({"status": "blocked", "error": "same_day_after_close with signal_lag_days=0 is look-ahead leakage"}, ensure_ascii=False))
        return 1
    try:
        execution_mode, timing_warnings = normalize_execution_mode(args.feature_cutoff_mode)
    except ValueError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 1
    if int(args.signal_lag_days) < 0:
        print(json.dumps({"status": "blocked", "error": "signal_lag_days must be non-negative"}, ensure_ascii=False))
        return 1
    output_dir = Path(args.output_dir)
    freeze_report = validate_research_input(args.data_dir, args.data_freeze_dir, args.require_data_freeze)
    if freeze_report.error_count > 0:
        print(json.dumps({"error": "data freeze validation failed", "freeze_validation_status": freeze_report.status}, ensure_ascii=False))
        return 1
    if args.data_freeze_dir:
        args.data_dir = str(Path(args.data_freeze_dir) / "data")
    portfolio_policy, policy_gate = _resolve_portfolio_policy(args)
    if policy_gate.get("blocked"):
        print(json.dumps({"error": "portfolio policy certification gate failed", "portfolio_policy_gate": policy_gate}, ensure_ascii=False))
        return 1
    loader = AShareDataLoader(
        data_dir=args.data_dir,
        device="cpu",
        universe_file=args.universe_file,
        universe_name=args.universe_name,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        min_listing_days=args.min_listing_days,
        exclude_st=args.exclude_st,
        active_security_mask_path=args.active_mask_path,
        corporate_action_aware=args.corporate_action_aware,
        corporate_action_dir=args.corporate_action_dir,
        target_return_mode=args.target_return_mode,
        corporate_action_cash_field=args.corporate_action_cash_field,
        corporate_action_application_mode=args.corporate_action_application_date_mode,
    ).load_data()
    store = LocalFactorStore(args.factor_store_dir)
    factor_id = select_factor_id(
        store,
        args.factor_id,
        latest_approved=args.latest_approved,
        factor_type=args.factor_type,
    )
    factor_meta = describe_factor(store, factor_id)
    values = store.load_factor_values(factor_id)
    factors = factor_values_to_matrix(values, loader.ts_codes, loader.trade_dates)
    factors = apply_signal_lag(factors, int(args.signal_lag_days))
    policy_context = _portfolio_policy_context(portfolio_policy, policy_gate)

    simulator = AShareBacktestSimulator(
        initial_cash=args.initial_cash,
        top_n=args.top_n,
        max_weight=args.max_weight,
        portfolio_method=args.portfolio_method,
        index_code=args.index_code,
        risk_aversion=args.risk_aversion,
        turnover_penalty=args.turnover_penalty,
        max_turnover=args.max_turnover,
        max_industry_active_weight=args.max_industry_active_weight,
        max_tracking_error=args.max_tracking_error,
        factor_id=factor_id,
        use_factor_risk_model=args.use_factor_risk_model,
        risk_model_lookback=args.risk_model_lookback,
        risk_model_shrinkage=args.risk_model_shrinkage,
        attribution=args.attribution,
        max_style_exposure=args.max_style_exposure,
        max_active_style_exposure=args.max_active_style_exposure,
        max_factor_risk_contribution=args.max_factor_risk_contribution,
        capacity_aware=args.capacity_aware,
        capacity_lookback=args.capacity_lookback,
        max_participation=args.max_participation,
        impact_base_bps=args.impact_base_bps,
        impact_power=args.impact_power,
        execution_buckets=tuple(item.strip() for item in args.execution_buckets.split(",") if item.strip()),
        time_contract=BacktestTimeContract(signal_lag_days=int(args.signal_lag_days)),
    )
    result = simulator.simulate(factors, loader)
    result.metrics["data_freeze_enabled"] = 1.0 if args.data_freeze_dir else 0.0
    result.metrics["data_hash_drift_count"] = float(freeze_report.error_count)
    result.metrics["signal_lag_days"] = float(args.signal_lag_days)
    result.metrics["execution_timing_mode"] = execution_mode
    result.metrics["execution_timing_warnings"] = timing_warnings
    if args.point_in_time and "active_mask" in loader.raw_data_cache:
        active_mask = loader.raw_data_cache["active_mask"]
        result.metrics["active_universe_coverage"] = float(active_mask.mean().item()) if active_mask.numel() else 0.0
    corporate_paths: dict[str, str | None] = {
        "corporate_action_report_path": None,
        "total_return_report_path": None,
        "adjustment_reconciliation_path": None,
    }
    if args.corporate_action_aware or args.corporate_action_report_dir:
        corporate_paths, corporate_summary = _write_corporate_action_artifacts(args, loader)
        result.metrics.update(
            {
                "corporate_action_event_count": float(corporate_summary.get("event_count", 0) or 0),
                "implemented_action_count": float(corporate_summary.get("implemented_action_count", 0) or 0),
                "cash_dividend_amount": float(corporate_summary.get("cash_dividend_amount_per_share", 0.0) or 0.0),
                "stock_distribution_event_count": float(corporate_summary.get("stock_distribution_event_count", 0) or 0),
                "corporate_action_warning_count": float(corporate_summary.get("corporate_action_warning_count", 0) or 0),
                "corporate_action_error_count": float(corporate_summary.get("corporate_action_error_count", 0) or 0),
                "adjustment_reconciliation_warning_count": float(
                    corporate_summary.get("adjustment_reconciliation_warning_count", 0) or 0
                ),
                "adjustment_reconciliation_error_count": float(
                    corporate_summary.get("adjustment_reconciliation_error_count", 0) or 0
                ),
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "backtest_result.json").write_text(
        json.dumps(_backtest_payload(result, policy_context), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_jsonl(output_dir / "equity_curve.jsonl", result.snapshots)
    _write_jsonl(output_dir / "trades.jsonl", result.fills)
    risk_control_paths: dict[str, str | None] = {
        "risk_control_report_path": None,
        "risk_control_breaches_path": None,
        "risk_limit_usage_path": None,
        "risk_control_decisions_path": None,
        "accepted_orders_path": None,
        "rejected_orders_path": None,
        "clipped_orders_path": None,
        "kill_switch_state_path": None,
    }
    risk_control_summary: dict[str, object] = {"status": "not_run"}
    if args.risk_controls:
        risk_dir = Path(args.risk_control_dir) if args.risk_control_dir else output_dir / "risk_controls"
        state_dir = risk_dir / "state"
        if args.risk_state_reset_each_run and state_dir.exists():
            import shutil

            shutil.rmtree(state_dir)
        report, _split, paths = evaluate_order_records(
            result.fills,
            policy_path=args.risk_policy_path,
            policy_profile=args.risk_policy_profile,
            state_dir=state_dir,
            output_dir=risk_dir,
            batch_id=f"backtest_{factor_id}",
            trade_date=loader.trade_dates[-1] if loader.trade_dates else "",
            scope="order",
            allow_clipping=args.risk_allow_clipping,
        )
        risk_control_paths = {key: str(value) for key, value in paths.items()}
        risk_control_summary = {
            "status": report.status,
            "accepted_orders": report.accepted_orders,
            "rejected_orders": report.rejected_orders,
            "clipped_orders": report.clipped_orders,
            "warning_count": report.warning_count,
            "error_count": report.error_count,
            "blocker_count": report.blocker_count,
        }
        result.metrics.update(
            {
                "risk_control_rejected_orders": float(report.rejected_orders),
                "risk_control_clipped_orders": float(report.clipped_orders),
                "risk_control_warning_count": float(report.warning_count),
                "risk_control_error_count": float(report.error_count),
            }
        )
        (output_dir / "backtest_result.json").write_text(json.dumps(_backtest_payload(result, policy_context), ensure_ascii=False, indent=2), encoding="utf-8")
        if args.risk_fail_on_breach and report.rejected_orders > 0:
            print(json.dumps({"error": "risk controls rejected backtest orders", **risk_control_summary}, ensure_ascii=False))
            return 1
    risk_exposures_path = None
    risk_decomposition_path = None
    return_attribution_path = None
    if simulator.risk_exposure_rows:
        risk_exposures_path = output_dir / "risk_exposures.jsonl"
        _write_dict_jsonl(risk_exposures_path, simulator.risk_exposure_rows)
    if simulator.risk_decomposition_rows:
        risk_decomposition_path = output_dir / "risk_decomposition.jsonl"
        _write_dict_jsonl(risk_decomposition_path, simulator.risk_decomposition_rows)
    if simulator.return_attribution_rows:
        return_attribution_path = output_dir / "return_attribution.jsonl"
        _write_dict_jsonl(return_attribution_path, simulator.return_attribution_rows)
    risk_report_path = None
    risk_report_md_path = None
    optimization_result_path = None
    if args.portfolio_method == "risk_aware":
        risk_dir = Path(args.risk_report_dir) if args.risk_report_dir else output_dir
        if simulator.risk_reports:
            risk_json, risk_md = write_risk_report(simulator.risk_reports[-1], risk_dir)
            risk_report_path = str(risk_json)
            risk_report_md_path = str(risk_md)
            if args.use_factor_risk_model:
                risk_model_json, risk_model_md = write_risk_model_report(simulator.risk_reports[-1], risk_dir)
                risk_report_path = str(risk_model_json)
                risk_report_md_path = str(risk_model_md)
        if simulator.optimization_results:
            optimization_result_path = output_dir / "optimization_result.json"
            optimization_result_path.write_text(
                json.dumps(
                    {
                        "factor_id": factor_id,
                        "factor_type": factor_meta["factor_type"],
                        "component_factor_ids": factor_meta["component_factor_ids"],
                        "portfolio_method": args.portfolio_method,
                        "index_code": args.index_code,
                        "latest": simulator.optimization_results[-1].to_dict(),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

    capacity_report_path = None
    capacity_report_md_path = None
    execution_plan_paths: dict[str, str | None] = {
        "execution_plan_path": None,
        "execution_plan_md_path": None,
        "parent_orders_path": None,
        "child_orders_path": None,
        "child_fills_path": None,
        "execution_quality_path": None,
    }
    if args.capacity_aware and simulator.execution_plan_results:
        plan_dir = Path(args.execution_plan_dir) if args.execution_plan_dir else output_dir / "execution_plan"
        paths = write_execution_plan_report(simulator.execution_plan_results[-1], plan_dir)
        execution_plan_paths = {key: str(path) for key, path in paths.items()}
        if simulator.capacity_reports:
            capacity_json, capacity_md = write_capacity_report(simulator.capacity_reports[-1], plan_dir)
            capacity_report_path = str(capacity_json)
            capacity_report_md_path = str(capacity_md)

    leakage_paths: dict[str, str | None] = {
        "leakage_audit_report_path": None,
        "truncation_consistency_report_path": None,
    }
    leakage_gate_status = "not_run"
    if args.run_leakage_audit:
        from auto_alpha.validation.firewall.leakage_run_audit import main as leakage_audit_main

        leakage_dir = Path(args.leakage_audit_dir) if args.leakage_audit_dir else output_dir / "leakage_audit"
        audit_argv = [
            "--data-dir",
            args.data_dir,
            "--factor-store-dir",
            args.factor_store_dir,
            "--factor-id",
            factor_id,
            "--backtest-result-path",
            str(output_dir / "backtest_result.json"),
            "--output-dir",
            str(leakage_dir),
            "--as-of-date",
            loader.trade_dates[-1],
            "--cutoff-date",
            loader.trade_dates[-1],
            "--run-static-scan",
            "--run-truncation-test",
        ]
        if args.point_in_time:
            audit_argv.extend(["--point-in-time", "--feature-cutoff-mode", args.feature_cutoff_mode])
        if args.fail_on_leakage_blocker:
            audit_argv.append("--fail-on-blocker")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            exit_code = leakage_audit_main(audit_argv)
        if exit_code != 0:
            return exit_code
        report_path = leakage_dir / "leakage_audit_report.json"
        leakage_paths = {
            "leakage_audit_report_path": str(report_path),
            "truncation_consistency_report_path": str(leakage_dir / "truncation_consistency_report.json"),
        }
        if report_path.exists():
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            leakage_gate_status = str(payload.get("leakage_gate_status") or payload.get("status") or "unknown")
            result.metrics["leakage_warning_count"] = float(payload.get("warning_count", 0) or 0)
            result.metrics["leakage_blocker_count"] = float(payload.get("blocker_count", 0) or 0)
            (output_dir / "backtest_result.json").write_text(json.dumps(_backtest_payload(result, policy_context), ensure_ascii=False, indent=2), encoding="utf-8")

    settlement_paths: dict[str, str | None] = {
        "settlement_report_path": None,
        "settlement_report_md_path": None,
        "settlement_events_path": None,
        "cash_buckets_path": None,
        "position_lots_path": None,
        "position_availability_path": None,
        "realized_pnl_path": None,
        "account_nav_path": None,
        "account_performance_report_path": None,
        "account_reconciliation_report_path": None,
        "fee_tax_report_path": None,
    }
    if args.settlement_aware or args.write_settlement_report:
        from auto_alpha.execution.trading.paper import LocalPaperAccount
        from auto_alpha.execution.settlement.engine import write_settlement_report

        settlement_dir = Path(args.settlement_dir) if args.settlement_dir else output_dir / "settlement"
        account = LocalPaperAccount(settlement_dir / "account")
        if account.load_state().initial_cash <= 0:
            account.reset(args.initial_cash)
        fills_by_date: dict[str, list[object]] = {}
        for fill in result.fills:
            fills_by_date.setdefault(fill.trade_date, []).append(fill)
        for trade_date, fills in sorted(fills_by_date.items()):
            prices = _prices_from_loader(loader, trade_date)
            account.apply_fills_settlement_aware(
                fills,
                data_dir=args.data_dir,
                trade_date=trade_date,
                profile=args.settlement_profile,
                prices=prices,
                cost_basis_method=args.cost_basis_method,
            )
        settle_date = args.settle_through_date or (loader.trade_dates[-1] if loader.trade_dates else "")
        state = account.settle(settle_date, prices=_prices_from_loader(loader, settle_date), profile=args.settlement_profile)
        if settle_date:
            state = account.mark_to_market(_prices_from_loader(loader, settle_date), settle_date)
        settlement_paths = write_settlement_report(state, settlement_dir, settle_date, profile_name=args.settlement_profile)
        reconciliation_payload = {}
        if settlement_paths.get("account_reconciliation_report_path"):
            reconciliation_payload = json.loads(Path(settlement_paths["account_reconciliation_report_path"]).read_text(encoding="utf-8"))
        fee_payload = {}
        if settlement_paths.get("fee_tax_report_path"):
            fee_payload = json.loads(Path(settlement_paths["fee_tax_report_path"]).read_text(encoding="utf-8"))
        result.metrics.update(
            {
                "settlement_aware": 1.0 if args.settlement_aware else 0.0,
                "pending_settlement_events": float(sum(event.get("status") == "pending" for event in state.settlement_events)),
                "failed_settlement_events": float(sum(event.get("status") == "failed" for event in state.settlement_events)),
                "available_cash": float(state.available_cash),
                "available_cash_min": float(state.available_cash),
                "realized_pnl": float(sum(float(record.get("realized_pnl", 0.0) or 0.0) for record in state.realized_pnl_ledger)),
                "unrealized_pnl": float(sum(float(position.unrealized_pnl) for position in state.positions.values())),
                "total_fees": float(fee_payload.get("total_fee_tax", 0.0) or 0.0),
                "total_commission": float(fee_payload.get("commission", 0.0) or 0.0),
                "total_stamp_duty": float(fee_payload.get("stamp_duty", 0.0) or 0.0),
                "total_transfer_fee": float(fee_payload.get("transfer_fee", 0.0) or 0.0),
                "total_slippage": float(fee_payload.get("slippage", 0.0) or 0.0),
                "nav_difference": float(reconciliation_payload.get("nav_difference", 0.0) or 0.0),
                "settlement_reconciliation_error_count": float(reconciliation_payload.get("error_count", 0) or 0),
            }
        )
        (output_dir / "backtest_result.json").write_text(json.dumps(_backtest_payload(result, policy_context), ensure_ascii=False, indent=2), encoding="utf-8")

    validation_paths: dict[str, str | None] = {
        "stress_backtest_report_path": None,
        "stress_backtest_report_md_path": None,
        "stress_backtest_results_path": None,
    }
    validation_summary: dict[str, object] = {"enabled": False}
    if args.validation_bundle or args.write_validation_stress_report:
        validation_dir = Path(args.validation_output_dir) if args.validation_output_dir else output_dir / "validation"
        stress_results, stress_summary = run_stress_backtest_bundle(
            result.metrics,
            cost_multipliers=_parse_float_list(args.stress_cost_multipliers),
            participations=_parse_float_list(args.stress_participations),
            settlement_profiles=_parse_str_list(args.stress_settlement_profiles),
            top_n_values=_parse_int_list(args.stress_top_n_values),
            max_weight_values=_parse_float_list(args.stress_max_weight_values),
        )
        validation_paths = write_stress_backtest_artifacts(validation_dir, stress_results, stress_summary)
        validation_summary = {
            "enabled": True,
            **stress_summary,
            "scenario_count": len(stress_results),
        }
        result.metrics.update(
            {
                "validation_bundle_enabled": 1.0,
                "stress_backtest_pass_ratio": float(stress_summary.get("stress_backtest_pass_ratio", 0.0) or 0.0),
                "stress_scenario_count": float(stress_summary.get("stress_scenario_count", 0) or 0),
            }
        )
        enriched = _backtest_payload(result, policy_context)
        enriched["validation_bundle"] = validation_summary
        enriched["validation_paths"] = validation_paths
        (output_dir / "backtest_result.json").write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "factor_id": factor_id,
        "factor_type": factor_meta["factor_type"],
        "component_factor_ids": factor_meta["component_factor_ids"],
        "portfolio_method": args.portfolio_method,
        "portfolio_policy_id": portfolio_policy.policy_id if portfolio_policy else None,
        "portfolio_policy_path": args.portfolio_policy_path,
        "portfolio_policy_gate": policy_gate,
        "output_dir": str(output_dir),
        "metrics": result.metrics,
        "n_snapshots": len(result.snapshots),
        "n_trades": len(result.fills),
        "risk_report_path": risk_report_path,
        "risk_report_md_path": risk_report_md_path,
        "optimization_result_path": str(optimization_result_path) if optimization_result_path else None,
        "risk_exposures_path": str(risk_exposures_path) if risk_exposures_path else None,
        "risk_decomposition_path": str(risk_decomposition_path) if risk_decomposition_path else None,
        "return_attribution_path": str(return_attribution_path) if return_attribution_path else None,
        "capacity_report_path": capacity_report_path,
        "capacity_report_md_path": capacity_report_md_path,
        "point_in_time": bool(args.point_in_time),
        "feature_cutoff_mode": args.feature_cutoff_mode,
        "signal_lag_days": args.signal_lag_days,
        "corporate_action_aware": bool(args.corporate_action_aware),
        "target_return_mode": args.target_return_mode,
        "settlement_aware": bool(args.settlement_aware),
        "settlement_profile": args.settlement_profile,
        "cost_basis_method": args.cost_basis_method,
        "leakage_gate_status": leakage_gate_status,
        **leakage_paths,
        **corporate_paths,
        **execution_plan_paths,
        **settlement_paths,
        "risk_controls": bool(args.risk_controls),
        "risk_control_summary": risk_control_summary,
        "data_freeze_dir": args.data_freeze_dir,
        "data_freeze_id": args.data_freeze_id or freeze_report.freeze_id,
        "data_freeze_hash": freeze_report.content_hash,
        "freeze_validation_status": freeze_report.status,
        "data_version_manifest_path": args.data_version_manifest_path,
        "freeze_validation_report_path": args.freeze_validation_report_path,
        **risk_control_paths,
        "validation_bundle": validation_summary,
        **validation_paths,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _resolve_portfolio_policy(args: argparse.Namespace):
    policy = None
    if args.active_optimizer_policy:
        if not args.model_registry_dir:
            return None, {"blocked": True, "reason": "model_registry_dir_required_for_active_optimizer_policy"}
        from auto_alpha.research.factors.registry import LocalModelRegistry

        active = LocalModelRegistry(args.model_registry_dir).latest_active_optimizer_policy()
        if active is None:
            return None, {"blocked": bool(args.require_certified_portfolio_policy), "reason": "active_optimizer_policy_not_found"}
        source = active.source_artifacts.get("certified_portfolio_policy_path") or active.source_artifacts.get("selected_portfolio_policy_path")
        if source and Path(source).exists():
            policy = load_portfolio_policy(source)
            args.portfolio_policy_path = str(source)
        else:
            policy = portfolio_policy_from_payload(active.metadata.get("portfolio_policy", active.metadata))
    elif args.portfolio_policy_path:
        policy = load_portfolio_policy(args.portfolio_policy_path)

    if policy is not None:
        args.portfolio_method = policy.portfolio_method
        args.index_code = policy.index_code
        args.top_n = policy.top_n
        args.max_weight = policy.max_weight
        args.risk_aversion = policy.risk_aversion
        args.turnover_penalty = policy.turnover_penalty
        args.max_turnover = policy.max_turnover
        args.max_industry_active_weight = policy.max_industry_active_weight
        args.max_tracking_error = policy.max_tracking_error
        args.use_factor_risk_model = policy.use_factor_risk_model
        args.risk_model_lookback = policy.risk_model_lookback
        args.risk_model_shrinkage = policy.risk_model_shrinkage
        args.max_style_exposure = policy.max_style_exposure
        args.max_active_style_exposure = policy.max_active_style_exposure
        args.max_factor_risk_contribution = policy.max_factor_risk_contribution

    gate = validate_certified_portfolio_policy(
        args.portfolio_policy_path,
        args.portfolio_certification_decision_path,
        require=args.require_certified_portfolio_policy,
    ).to_dict()
    if policy is not None and not args.portfolio_policy_path and policy.certification_status in {"certified", "conditional"}:
        gate.update({"certified": True, "status": policy.certification_status, "reasons": []})
    gate["blocked"] = bool(args.require_certified_portfolio_policy and gate.get("reasons"))
    return policy, gate


def _portfolio_policy_context(policy, gate: dict[str, object]) -> dict[str, object]:
    return {"policy": policy.to_dict() if policy is not None else None, "gate": gate}


def _backtest_payload(result, policy_context: dict[str, object]) -> dict[str, object]:
    payload = result.to_dict()
    payload["portfolio_policy"] = policy_context
    return payload


def _write_corporate_action_artifacts(args: argparse.Namespace, loader: AShareDataLoader) -> tuple[dict[str, str | None], dict[str, object]]:
    output_dir = Path(args.corporate_action_report_dir) if args.corporate_action_report_dir else Path(args.output_dir) / "corporate_actions"
    events_path = Path(args.corporate_action_dir) / "corporate_action_events.jsonl" if args.corporate_action_dir else None
    if events_path is not None and events_path.exists():
        events = [CorporateActionEvent(**record) for record in read_jsonl(events_path)]
    else:
        events = normalize_corporate_action_records(
            getattr(loader, "raw_corporate_actions", []),
            cash_field=args.corporate_action_cash_field,
        )
    paths = write_corporate_action_report(
        args.data_dir,
        events,
        output_dir,
        start_date=loader.trade_dates[0] if loader.trade_dates else "00000000",
        end_date=loader.trade_dates[-1] if loader.trade_dates else "99999999",
        total_return_mode="cash_reinvested",
        reconcile_adjustment=args.reconcile_adjustment_factors,
    )
    summary = json.loads(Path(paths["corporate_actions_report_path"]).read_text(encoding="utf-8"))
    return paths, summary


def _prices_from_loader(loader: AShareDataLoader, trade_date: str) -> dict[str, float]:
    if not trade_date or trade_date not in loader.trade_dates:
        return {}
    close = loader.raw_data_cache["close"].detach().cpu()
    date_idx = loader.trade_dates.index(trade_date)
    return {ts_code: float(close[idx, date_idx].item()) for idx, ts_code in enumerate(loader.ts_codes)}


def _parse_float_list(value: str | None) -> list[float]:
    return [float(item.strip()) for item in (value or "").split(",") if item.strip()]


def _parse_int_list(value: str | None) -> list[int]:
    return [int(float(item.strip())) for item in (value or "").split(",") if item.strip()]


def _parse_str_list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def apply_signal_lag(factors, signal_lag_days: int):
    """Move signal availability to the actual target-weight and execution date."""
    import torch

    lag = int(signal_lag_days)
    tensor = factors.detach().clone() if hasattr(factors, "detach") else torch.tensor(factors, dtype=torch.float32)
    if lag == 0:
        return tensor
    shifted = torch.full_like(tensor, float("nan"))
    if lag < tensor.shape[1]:
        shifted[:, lag:] = tensor[:, :-lag]
    return shifted


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "AShareBacktestSimulator",
    "AShareCostModel",
    "AShareTradingRules",
    "PortfolioBacktestResult",
    "PortfolioSnapshot",
    "TargetPosition",
    "TradeFill",
    "TradeOrder",
    "build_long_only_targets",
    "describe_factor",
    "factor_values_to_matrix",
    "select_factor_id",
    "targets_to_weight_matrix",
]
