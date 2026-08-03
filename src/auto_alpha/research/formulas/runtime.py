"""A-share factor research core."""

from auto_alpha.research.formulas.runtime_backtest import AShareFactorEvaluator
from auto_alpha.research.formulas.runtime_backtest import FactorEvaluationResult
from auto_alpha.research.formulas.runtime_data_loader import AShareDataLoader
from auto_alpha.research.formulas.runtime_factors import AShareFeatureEngineer
from auto_alpha.research.formulas.runtime_factors import FeatureEngineer
from auto_alpha.research.formulas.runtime_vm import StackVM
from auto_alpha.research.formulas.runtime_vocab import FEATURE_NAMES
from auto_alpha.research.formulas.runtime_vocab import FORMULA_VOCAB

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
        from auto_alpha.research.formulas.runtime_engine import FactorMiningEngine

        return FactorMiningEngine
    raise AttributeError(name)
