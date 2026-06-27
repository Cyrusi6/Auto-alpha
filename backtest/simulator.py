"""A-share long-only portfolio simulation."""

from __future__ import annotations

import math

import torch

from capacity_model import CapacityConfig, build_capacity_report
from execution import ExecutionOrder
from execution_plan import ExecutionPlanConfig, ExecutionPlanResult, build_execution_schedule, build_parent_orders_from_target_orders, simulate_child_orders
from portfolio_optimizer import OptimizationConfig, PortfolioOptimizer
from risk_model import (
    active_risk_decomposition,
    attribute_active_return,
    benchmark_weights_from_index_members,
    build_barra_like_risk_model,
    build_risk_report,
    estimate_return_covariance,
    portfolio_factor_exposure,
    portfolio_risk_decomposition,
)

from .cost import AShareCostModel
from .models import PortfolioBacktestResult, PortfolioSnapshot, TradeFill
from .portfolio import build_long_only_targets, targets_to_weight_matrix
from .rules import AShareTradingRules


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
        self.execution_plan_config = ExecutionPlanConfig(
            buckets=execution_buckets or ("open", "morning", "afternoon", "close"),
            max_child_participation=max_participation,
            capacity_lookback=capacity_lookback,
            impact_base_bps=impact_base_bps,
            impact_power=impact_power,
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
        close = loader.raw_data_cache["close"].detach().cpu()
        volume = loader.raw_data_cache.get("volume", torch.zeros_like(close)).detach().cpu()
        is_suspended = loader.raw_data_cache.get("is_suspended", torch.zeros_like(close)).detach().cpu()
        limit_up = loader.raw_data_cache.get("limit_up_flag", torch.zeros_like(close)).detach().cpu()
        limit_down = loader.raw_data_cache.get("limit_down_flag", torch.zeros_like(close)).detach().cpu()
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
        total_cost = 0.0
        covariance = estimate_return_covariance(loader)
        optimizer = PortfolioOptimizer(self.optimization_config)
        risk_metric_rows: list[dict[str, float]] = []
        factor_risk_model = (
            build_barra_like_risk_model(loader, lookback=self.risk_model_lookback, shrinkage=self.risk_model_shrinkage)
            if self.use_factor_risk_model
            else None
        )

        for date_idx, trade_date in enumerate(loader.trade_dates):
            if date_idx > 0:
                realized_return = float((current_weights * target_ret[:, date_idx - 1]).sum().item())
                equity *= 1.0 + realized_return

            if self.portfolio_method == "risk_aware":
                benchmark = benchmark_weights_from_index_members(loader, self.index_code, trade_date)
                opt_result = optimizer.optimize(
                    factor_tensor[:, date_idx],
                    current_weights=current_weights,
                    benchmark_weights=benchmark,
                    covariance=covariance,
                    loader=loader,
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
                    attribution_payload = (
                        attribute_active_return(
                            desired_weights,
                            benchmark,
                            target_ret[:, date_idx],
                            factor_risk_model.exposure_matrix,
                            factor_risk_model.factor_returns,
                            date_idx,
                        )
                        if self.attribution
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
                invested_weight = float(current_weights.sum().item())
                positions_value = equity * invested_weight
                cash = equity - positions_value
                daily_return = (equity / prev_equity - 1.0) if prev_equity > 0 else 0.0
                turnover = float(day_traded_value / max(equity, 1e-6))
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
                price = float(close[stock_idx, date_idx].item())
                suspended = bool(is_suspended[stock_idx, date_idx].item() > 0.5)
                is_limit_up = bool(limit_up[stock_idx, date_idx].item() > 0.5)
                is_limit_down = bool(limit_down[stock_idx, date_idx].item() > 0.5)
                if side == "BUY":
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
            invested_weight = float(current_weights.sum().item())
            positions_value = equity * invested_weight
            cash = equity - positions_value
            daily_return = (equity / prev_equity - 1.0) if prev_equity > 0 else 0.0
            turnover = float(day_traded_value / max(equity, 1e-6))
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
        if risk_metric_rows:
            metrics.update(_average_risk_metrics(risk_metric_rows))
        if self.risk_decomposition_rows:
            metrics.update(_average_factor_risk_metrics(self.risk_decomposition_rows, self.risk_exposure_rows))
        if self.execution_plan_results:
            metrics.update(_average_execution_quality(self.execution_plan_results))
        return PortfolioBacktestResult(snapshots=snapshots, fills=fills, metrics=metrics)

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
            if fill.status == "REJECTED" and fill.reason in {"suspended", "limit_up", "limit_down", "t_plus_one", "volume_limit"}
        )
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
