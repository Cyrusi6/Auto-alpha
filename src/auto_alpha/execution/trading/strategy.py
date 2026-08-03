"""A-share strategy order generation."""

from auto_alpha.execution.trading.strategy_config import AShareStrategyConfig
from auto_alpha.execution.trading.strategy_portfolio import StrategyTargetBook
from auto_alpha.execution.trading.strategy_risk import AShareRiskEngine

__all__ = [
    "AShareRiskEngine",
    "AShareStrategyConfig",
    "AShareStrategyRunner",
    "StrategyTargetBook",
]


def __getattr__(name: str):
    if name == "AShareStrategyRunner":
        from auto_alpha.execution.trading.strategy_runner import AShareStrategyRunner

        return AShareStrategyRunner
    raise AttributeError(name)
