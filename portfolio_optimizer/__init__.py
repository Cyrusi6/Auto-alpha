"""Benchmark-aware A-share portfolio optimization."""

from .models import OptimizationConfig, OptimizationResult
from .optimizer import PortfolioOptimizer
from .policy import (
    PortfolioPolicy,
    PortfolioPolicyLoadResult,
    build_portfolio_policy,
    from_portfolio_policy,
    load_portfolio_policy,
    make_portfolio_policy_id,
    portfolio_policy_from_payload,
    portfolio_policy_hash,
    validate_certified_portfolio_policy,
    write_portfolio_policy,
)

__all__ = [
    "OptimizationConfig",
    "OptimizationResult",
    "PortfolioOptimizer",
    "PortfolioPolicy",
    "PortfolioPolicyLoadResult",
    "build_portfolio_policy",
    "from_portfolio_policy",
    "load_portfolio_policy",
    "make_portfolio_policy_id",
    "portfolio_policy_from_payload",
    "portfolio_policy_hash",
    "validate_certified_portfolio_policy",
    "write_portfolio_policy",
]
