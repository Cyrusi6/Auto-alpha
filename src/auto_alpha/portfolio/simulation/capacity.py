"""A-share capacity and impact cost analysis."""

from auto_alpha.portfolio.simulation.capacity_estimator import estimate_portfolio_capacity
from auto_alpha.portfolio.simulation.capacity_estimator import estimate_security_capacity
from auto_alpha.portfolio.simulation.capacity_estimator import rank_capacity
from auto_alpha.portfolio.simulation.capacity_impact import estimate_capacity_adjusted_order
from auto_alpha.portfolio.simulation.capacity_impact import estimate_impact_cost
from auto_alpha.portfolio.simulation.capacity_models import CapacityConfig
from auto_alpha.portfolio.simulation.capacity_models import CapacityReport
from auto_alpha.portfolio.simulation.capacity_models import PortfolioCapacity
from auto_alpha.portfolio.simulation.capacity_models import SecurityCapacity
from auto_alpha.portfolio.simulation.capacity_report import build_capacity_report
from auto_alpha.portfolio.simulation.capacity_report import write_capacity_report

__all__ = [
    "CapacityConfig",
    "CapacityReport",
    "PortfolioCapacity",
    "SecurityCapacity",
    "build_capacity_report",
    "estimate_capacity_adjusted_order",
    "estimate_impact_cost",
    "estimate_portfolio_capacity",
    "estimate_security_capacity",
    "rank_capacity",
    "write_capacity_report",
]
