"""Benchmark-aware A-share portfolio optimization."""

from auto_alpha.portfolio.construction.optimizer_models import OptimizationConfig
from auto_alpha.portfolio.construction.optimizer_models import OptimizationResult
from auto_alpha.portfolio.construction.optimizer_optimizer import PortfolioOptimizer
from auto_alpha.portfolio.construction.optimizer_policy import PortfolioPolicy
from auto_alpha.portfolio.construction.optimizer_policy import PortfolioPolicyLoadResult
from auto_alpha.portfolio.construction.optimizer_policy import build_portfolio_policy
from auto_alpha.portfolio.construction.optimizer_policy import from_portfolio_policy
from auto_alpha.portfolio.construction.optimizer_policy import load_portfolio_policy
from auto_alpha.portfolio.construction.optimizer_policy import make_portfolio_policy_id
from auto_alpha.portfolio.construction.optimizer_policy import portfolio_policy_from_payload
from auto_alpha.portfolio.construction.optimizer_policy import portfolio_policy_hash
from auto_alpha.portfolio.construction.optimizer_policy import validate_certified_portfolio_policy
from auto_alpha.portfolio.construction.optimizer_policy import write_portfolio_policy

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
