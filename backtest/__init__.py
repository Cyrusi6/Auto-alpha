"""A-share portfolio simulation package."""

from .cost import AShareCostModel
from .io import factor_values_to_matrix, select_factor_id
from .models import PortfolioBacktestResult, PortfolioSnapshot, TargetPosition, TradeFill, TradeOrder
from .portfolio import build_long_only_targets, targets_to_weight_matrix
from .rules import AShareTradingRules
from .simulator import AShareBacktestSimulator

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
    "factor_values_to_matrix",
    "select_factor_id",
    "targets_to_weight_matrix",
]
