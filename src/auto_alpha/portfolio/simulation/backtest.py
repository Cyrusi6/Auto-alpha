"""A-share portfolio simulation package."""

from auto_alpha.portfolio.simulation.backtest_cost import AShareCostModel
from auto_alpha.portfolio.simulation.backtest_io import describe_factor
from auto_alpha.portfolio.simulation.backtest_io import factor_values_to_matrix
from auto_alpha.portfolio.simulation.backtest_io import select_factor_id
from auto_alpha.portfolio.simulation.backtest_models import PortfolioBacktestResult
from auto_alpha.portfolio.simulation.backtest_models import PortfolioSnapshot
from auto_alpha.portfolio.simulation.backtest_models import TargetPosition
from auto_alpha.portfolio.simulation.backtest_models import TradeFill
from auto_alpha.portfolio.simulation.backtest_models import TradeOrder
from auto_alpha.portfolio.simulation.backtest_portfolio import build_long_only_targets
from auto_alpha.portfolio.simulation.backtest_portfolio import targets_to_weight_matrix
from auto_alpha.portfolio.simulation.backtest_rules import AShareTradingRules
from auto_alpha.portfolio.simulation.backtest_simulator import AShareBacktestSimulator

__all__ = [
    "AShareBacktestSimulator",
    "AShareCostModel",
    "AShareTradingRules",
    "PortfolioBacktestResult",
    "PortfolioSnapshot",
    "TargetPosition",
    "TradeFill",
    "TradeOrder",
    "build_long_only_targets",
    "describe_factor",
    "factor_values_to_matrix",
    "select_factor_id",
    "targets_to_weight_matrix",
]
