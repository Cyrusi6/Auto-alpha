"""Governed factor-certified portfolio auto_alpha.research.discovery.studies."""

from auto_alpha.portfolio.construction.research_admission import validate_factor_certified_records
from auto_alpha.portfolio.construction.research_combination import CombinationFit
from auto_alpha.portfolio.construction.research_combination import build_combined_signal
from auto_alpha.portfolio.construction.research_combination import fit_factor_combination
from auto_alpha.portfolio.construction.research_contracts import FACTOR_CERTIFIED_STATUS
from auto_alpha.portfolio.construction.research_contracts import PortfolioResearchError
from auto_alpha.portfolio.construction.research_contracts import PortfolioResearchPolicy
from auto_alpha.portfolio.construction.research_contracts import StressScenario
from auto_alpha.portfolio.construction.research_engine import PortfolioResearchData
from auto_alpha.portfolio.construction.research_engine import evaluate_portfolio_research

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
