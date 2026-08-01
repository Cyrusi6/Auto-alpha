"""Risk constraint checks for benchmark-aware A-share portfolios."""

from __future__ import annotations

import torch

from .covariance import estimate_return_covariance, tracking_error
from .exposures import active_exposure, portfolio_exposure
from .models import RiskConstraintConfig


def check_risk_constraints(weights, benchmark_weights, loader, config: RiskConstraintConfig) -> tuple[bool, list[str], dict[str, object]]:
    weight_tensor = _to_tensor(weights)
    benchmark = _to_tensor(benchmark_weights)
    portfolio = portfolio_exposure(weight_tensor, loader)
    active = active_exposure(weight_tensor, benchmark, loader)
    cov = estimate_return_covariance(loader)
    te = tracking_error(weight_tensor, benchmark, cov)
    active_weight = float(torch.abs(weight_tensor - benchmark).sum().item())
    industry_active = max((abs(value) for value in active.industry_weights.values()), default=0.0)
    violations: list[str] = []
    if portfolio.top_weight > config.max_weight + 1e-6:
        violations.append("max_weight")
    if portfolio.n_positions < config.min_names:
        violations.append("min_names")
    if portfolio.n_positions > config.max_names:
        violations.append("max_names")
    if portfolio.concentration_hhi > config.max_hhi + 1e-9:
        violations.append("max_hhi")
    if industry_active > config.max_industry_active_weight + 1e-9:
        violations.append("max_industry_active_weight")
    if active_weight > config.max_total_active_weight + 1e-9:
        violations.append("max_total_active_weight")
    if te > config.max_tracking_error + 1e-9:
        violations.append("max_tracking_error")
    checks = {
        "top_weight": float(portfolio.top_weight),
        "n_positions": float(portfolio.n_positions),
        "hhi": float(portfolio.concentration_hhi),
        "industry_active_max": float(industry_active),
        "total_active_weight": float(active_weight),
        "tracking_error": float(te),
        "violations": list(violations),
    }
    return not violations, violations, checks


def _to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)
