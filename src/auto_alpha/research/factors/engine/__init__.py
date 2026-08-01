"""A-share factor preprocessing, correlation, and gate utilities."""

from .correlation import (
    factor_correlation,
    factor_correlation_matrix,
    find_similar_factors,
    load_existing_factor_matrices,
    max_abs_correlation,
    pairwise_correlation_table,
)
from .gate import FactorGateConfig, FactorGateDecision, evaluate_factor_gate
from .pipeline import FactorResearchPipeline, FactorResearchResult
from .transforms import (
    SUPPORTED_TRANSFORMS,
    cs_winsorize_mad,
    cs_zscore,
    neutralize_industry,
    neutralize_industry_size,
    neutralize_market_cap,
    preprocess_factor,
)

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
