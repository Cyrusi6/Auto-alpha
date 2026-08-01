"""A-share portfolio risk model utilities."""

from .constraints import check_risk_constraints
from .covariance import estimate_return_covariance, portfolio_volatility, tracking_error
from .attribution import attribute_active_return, attribute_portfolio_return, brinson_industry_attribution
from .decomposition import (
    active_risk_decomposition,
    factor_risk_contribution,
    portfolio_factor_exposure,
    portfolio_risk_decomposition,
    specific_risk_contribution,
)
from .exposures import (
    active_exposure,
    benchmark_exposure,
    benchmark_weights_from_index_members,
    build_security_exposures,
    portfolio_exposure,
)
from .factor_model import (
    build_barra_like_risk_model,
    estimate_factor_covariance,
    estimate_factor_returns,
    estimate_specific_risk,
)
from .industry import build_industry_exposures
from .models import (
    BenchmarkExposure,
    FactorExposureMatrix,
    FactorModelSpec,
    FactorReturnSeries,
    FactorRiskModel,
    PortfolioExposure,
    RiskConstraintConfig,
    RiskMetrics,
    RiskReport,
    SecurityExposure,
)
from .report import build_risk_model_report, build_risk_report, write_risk_model_report, write_risk_report
from .style import STYLE_FACTOR_NAMES, build_style_exposures

__all__ = [
    "BenchmarkExposure",
    "PortfolioExposure",
    "RiskConstraintConfig",
    "RiskMetrics",
    "RiskReport",
    "SecurityExposure",
    "active_exposure",
    "active_risk_decomposition",
    "attribute_active_return",
    "attribute_portfolio_return",
    "benchmark_exposure",
    "benchmark_weights_from_index_members",
    "brinson_industry_attribution",
    "build_barra_like_risk_model",
    "build_industry_exposures",
    "build_risk_model_report",
    "build_risk_report",
    "build_security_exposures",
    "build_style_exposures",
    "check_risk_constraints",
    "estimate_return_covariance",
    "estimate_factor_covariance",
    "estimate_factor_returns",
    "estimate_specific_risk",
    "factor_risk_contribution",
    "FactorExposureMatrix",
    "FactorModelSpec",
    "FactorReturnSeries",
    "FactorRiskModel",
    "portfolio_exposure",
    "portfolio_factor_exposure",
    "portfolio_risk_decomposition",
    "portfolio_volatility",
    "specific_risk_contribution",
    "STYLE_FACTOR_NAMES",
    "tracking_error",
    "write_risk_model_report",
    "write_risk_report",
]
