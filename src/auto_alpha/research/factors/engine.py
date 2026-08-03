"""A-share factor preprocessing, correlation, and gate utilities."""

from auto_alpha.research.factors.engine_correlation import factor_correlation
from auto_alpha.research.factors.engine_correlation import factor_correlation_matrix
from auto_alpha.research.factors.engine_correlation import find_similar_factors
from auto_alpha.research.factors.engine_correlation import load_existing_factor_matrices
from auto_alpha.research.factors.engine_correlation import max_abs_correlation
from auto_alpha.research.factors.engine_correlation import pairwise_correlation_table
from auto_alpha.research.factors.engine_gate import FactorGateConfig
from auto_alpha.research.factors.engine_gate import FactorGateDecision
from auto_alpha.research.factors.engine_gate import evaluate_factor_gate
from auto_alpha.research.factors.engine_pipeline import FactorResearchPipeline
from auto_alpha.research.factors.engine_pipeline import FactorResearchResult
from auto_alpha.research.factors.engine_transforms import SUPPORTED_TRANSFORMS
from auto_alpha.research.factors.engine_transforms import cs_winsorize_mad
from auto_alpha.research.factors.engine_transforms import cs_zscore
from auto_alpha.research.factors.engine_transforms import neutralize_industry
from auto_alpha.research.factors.engine_transforms import neutralize_industry_size
from auto_alpha.research.factors.engine_transforms import neutralize_market_cap
from auto_alpha.research.factors.engine_transforms import preprocess_factor

__all__ = [
    "SUPPORTED_TRANSFORMS",
    "FactorGateConfig",
    "FactorGateDecision",
    "FactorResearchPipeline",
    "FactorResearchResult",
    "cs_winsorize_mad",
    "cs_zscore",
    "evaluate_factor_gate",
    "factor_correlation",
    "factor_correlation_matrix",
    "find_similar_factors",
    "load_existing_factor_matrices",
    "max_abs_correlation",
    "pairwise_correlation_table",
    "neutralize_industry",
    "neutralize_industry_size",
    "neutralize_market_cap",
    "preprocess_factor",
]
