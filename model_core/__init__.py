"""A-share factor research core."""

from .backtest import AShareFactorEvaluator, FactorEvaluationResult
from .data_loader import AShareDataLoader
from .factors import AShareFeatureEngineer, FeatureEngineer
from .vm import StackVM
from .vocab import FEATURE_NAMES, FORMULA_VOCAB

__all__ = [
    "AShareDataLoader",
    "AShareFeatureEngineer",
    "AShareFactorEvaluator",
    "FEATURE_NAMES",
    "FORMULA_VOCAB",
    "FactorEvaluationResult",
    "FactorMiningEngine",
    "FeatureEngineer",
    "StackVM",
]


def __getattr__(name: str):
    if name == "FactorMiningEngine":
        from .engine import FactorMiningEngine

        return FactorMiningEngine
    raise AttributeError(name)
