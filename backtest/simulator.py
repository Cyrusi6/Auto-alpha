"""A-share long-only portfolio simulation."""

from __future__ import annotations

import math

import torch

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
        cost_model: AShareCostModel | None = None,
        trading_rules: AShareTradingRules | None = None,
    ):
        self.initial_cash = float(initial_cash)
        self.top_n = int(top_n)
        self.max_weight = float(max_weight)
        self.cost_model = cost_model or AShareCostModel()
        self.trading_rules = trading_rules or AShareTradingRules(max_position_weight=max_weight)

    def simulate(self, factors, loader) -> PortfolioBacktestResult:
        factor_tensor = factors.detach().cpu() if hasattr(factors, "detach") else torch.tensor(factors)
        close = loader.raw_data_cache["close"].detach().cpu()
        volume = loader.raw_data_cache.get("volume", torch.zeros_like(close)).detach().cpu()
        is_suspended = loader.raw_data_cache.get("is_suspended", torch.zeros_like(close)).detach().cpu()
        limit_up = loader.raw_data_cache.get("limit_up_flag", torch.zeros_like(close)).detach().cpu()
        limit_down = loader.raw_data_cache.get("limit_down_flag", torch.zeros_like(close)).detach().cpu()
        target_ret = loader.target_ret.detach().cpu()
        targets_by_date = build_long_only_targets(
            factor_tensor,
            loader.ts_codes,
            loader.trade_dates,
            top_n=self.top_n,
            max_weight=self.max_weight,
        )
        target_weights = targets_to_weight_matrix(targets_by_date, loader.ts_codes, loader.trade_dates)

        current_weights = torch.zeros(len(loader.ts_codes), dtype=torch.float32)
        first_buy_index: dict[int, int] = {}
        equity = self.initial_cash
        prev_equity = self.initial_cash
        snapshots: list[PortfolioSnapshot] = []
        fills: list[TradeFill] = []
        total_cost = 0.0

        for date_idx, trade_date in enumerate(loader.trade_dates):
            if date_idx > 0:
                realized_return = float((current_weights * target_ret[:, date_idx - 1]).sum().item())
                equity *= 1.0 + realized_return

            desired_weights = target_weights[:, date_idx].clone()
            desired_weights = torch.clamp(desired_weights, 0.0, self.max_weight)
            deltas = desired_weights - current_weights
            day_cost = 0.0
            day_traded_value = 0.0
            adjusted_weights = current_weights.clone()

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
        return PortfolioBacktestResult(snapshots=snapshots, fills=fills, metrics=metrics)

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
