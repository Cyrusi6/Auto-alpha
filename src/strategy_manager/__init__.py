"""A-share strategy order generation."""

from .config import AShareStrategyConfig
from .portfolio import StrategyTargetBook
from .risk import AShareRiskEngine

__all__ = [
    "AShareRiskEngine",
    "AShareStrategyConfig",
    "AShareStrategyRunner",
    "StrategyTargetBook",
]


def __getattr__(name: str):
    if name == "AShareStrategyRunner":
        from .runner import AShareStrategyRunner

        return AShareStrategyRunner
    raise AttributeError(name)
