"""Deterministic long-only benchmark-aware portfolio optimizer."""

from __future__ import annotations

import torch

from risk_model import RiskConstraintConfig, check_risk_constraints, tracking_error

from .models import OptimizationConfig, OptimizationResult


class PortfolioOptimizer:
    def __init__(self, config: OptimizationConfig | None = None):
        self.config = config or OptimizationConfig()

    def optimize(
        self,
        alpha_scores,
        current_weights,
        benchmark_weights,
        covariance,
        loader,
    ) -> OptimizationResult:
        alpha = _finite_tensor(alpha_scores, len(loader.ts_codes))
        current = _finite_tensor(current_weights, len(loader.ts_codes))
        benchmark = _finite_tensor(benchmark_weights, len(loader.ts_codes))
        cov = covariance.detach().cpu().to(dtype=torch.float32) if hasattr(covariance, "detach") else torch.tensor(covariance, dtype=torch.float32)
        weights = self._initial_weights(alpha, benchmark)
        weights = self._apply_turnover(weights, current)
        weights = self._apply_tracking_error(weights, benchmark, cov)
        weights = self._finalize(weights)

        predicted_alpha = float((weights * alpha).sum().item())
        predicted_risk = float(max((weights @ cov @ weights).item(), 0.0) ** 0.5)
        te = tracking_error(weights, benchmark, cov)
        turnover = float(torch.abs(weights - current).sum().item())
        constraint_config = RiskConstraintConfig(
            max_weight=self.config.max_weight,
            max_industry_active_weight=self.config.max_industry_active_weight,
            max_tracking_error=self.config.max_tracking_error,
            max_turnover=self.config.max_turnover,
            min_names=self.config.min_names,
            max_names=self.config.max_names,
        )
        _, violations, checks = check_risk_constraints(weights, benchmark, loader, constraint_config)
        if turnover > self.config.max_turnover + 1e-9 and "max_turnover" not in violations:
            violations.append("max_turnover")
        objective = predicted_alpha - self.config.risk_aversion * predicted_risk - self.config.turnover_penalty * turnover
        return OptimizationResult(
            weights={
                ts_code: float(weights[idx].item())
                for idx, ts_code in enumerate(loader.ts_codes)
                if float(weights[idx].item()) > 1e-10
            },
            objective_value=float(objective),
            predicted_alpha=predicted_alpha,
            predicted_risk=predicted_risk,
            tracking_error=float(te),
            turnover=turnover,
            violations=violations,
            diagnostics={
                "checks": checks,
                "weight_sum": float(weights.sum().item()),
                "cash_weight": float(max(0.0, 1.0 - weights.sum().item())),
                "selected_names": int((weights > 1e-9).sum().item()),
            },
        )

    def _initial_weights(self, alpha: torch.Tensor, benchmark: torch.Tensor) -> torch.Tensor:
        n = alpha.numel()
        max_names = max(1, min(self.config.max_names, n))
        valid = torch.isfinite(alpha)
        if not bool(valid.any()):
            return torch.zeros(n, dtype=torch.float32)
        order = torch.argsort(torch.where(valid, alpha, torch.full_like(alpha, -1e9)), descending=True)
        selected = order[:max_names]
        budget = min(max(0.0, 1.0 - self.config.cash_weight), max_names * self.config.max_weight)
        rank_scores = torch.linspace(float(max_names), 1.0, steps=max_names, dtype=torch.float32)
        alpha_weights = torch.zeros(n, dtype=torch.float32)
        alpha_weights[selected] = rank_scores / torch.clamp(rank_scores.sum(), min=1e-6) * budget

        benchmark_slice = torch.zeros(n, dtype=torch.float32)
        benchmark_slice[selected] = torch.clamp(benchmark[selected], min=0.0)
        if float(benchmark_slice.sum().item()) > 1e-12:
            benchmark_slice = benchmark_slice / benchmark_slice.sum() * budget
        else:
            benchmark_slice = alpha_weights.clone()

        blend = max(0.0, min(1.0, self.config.benchmark_weight))
        weights = (1.0 - blend) * alpha_weights + blend * benchmark_slice
        return self._finalize(weights)

    def _apply_turnover(self, weights: torch.Tensor, current: torch.Tensor) -> torch.Tensor:
        turnover = float(torch.abs(weights - current).sum().item())
        if turnover <= self.config.max_turnover or turnover <= 1e-12:
            return weights
        ratio = max(0.0, min(1.0, self.config.max_turnover / turnover))
        return self._finalize(current + (weights - current) * ratio)

    def _apply_tracking_error(self, weights: torch.Tensor, benchmark: torch.Tensor, cov: torch.Tensor) -> torch.Tensor:
        result = weights
        for _ in range(10):
            te = tracking_error(result, benchmark, cov)
            if te <= self.config.max_tracking_error + 1e-9:
                break
            result = self._finalize(0.75 * result + 0.25 * benchmark)
        return result

    def _finalize(self, weights: torch.Tensor) -> torch.Tensor:
        result = torch.nan_to_num(weights.clone().to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)
        if self.config.long_only:
            result = torch.clamp(result, min=0.0)
        result = torch.clamp(result, max=self.config.max_weight)
        if self.config.max_names > 0 and int((result > 1e-12).sum().item()) > self.config.max_names:
            keep = torch.argsort(result, descending=True)[: self.config.max_names]
            mask = torch.zeros_like(result)
            mask[keep] = 1.0
            result = result * mask
        max_budget = max(0.0, 1.0 - self.config.cash_weight)
        total = float(result.sum().item())
        if total > max_budget and total > 1e-12:
            result = result / total * max_budget
            result = torch.clamp(result, max=self.config.max_weight)
        return torch.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)


def _finite_tensor(values, n: int) -> torch.Tensor:
    tensor = values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)
    tensor = tensor.reshape(n)
    return torch.nan_to_num(tensor, nan=0.0, posinf=0.0, neginf=0.0)
