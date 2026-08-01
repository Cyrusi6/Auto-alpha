"""Governed factor-certified portfolio auto_alpha.research.discovery.studies."""

from .admission import validate_factor_certified_records
from .combination import CombinationFit, build_combined_signal, fit_factor_combination
from .contracts import (
    FACTOR_CERTIFIED_STATUS,
    PortfolioResearchError,
    PortfolioResearchPolicy,
    StressScenario,
)
from .engine import PortfolioResearchData, evaluate_portfolio_research

__all__ = [
    "CombinationFit",
    "FACTOR_CERTIFIED_STATUS",
    "PortfolioResearchData",
    "PortfolioResearchError",
    "PortfolioResearchPolicy",
    "StressScenario",
    "build_combined_signal",
    "evaluate_portfolio_research",
    "fit_factor_combination",
    "validate_factor_certified_records",
]
