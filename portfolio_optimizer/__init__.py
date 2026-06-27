"""Benchmark-aware A-share portfolio optimization."""

from .models import OptimizationConfig, OptimizationResult
from .optimizer import PortfolioOptimizer

__all__ = ["OptimizationConfig", "OptimizationResult", "PortfolioOptimizer"]
