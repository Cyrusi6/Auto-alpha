"""A-share portfolio risk model utilities."""

from auto_alpha.portfolio.risk.model_constraints import check_risk_constraints
from auto_alpha.portfolio.risk.model_covariance import estimate_return_covariance
from auto_alpha.portfolio.risk.model_covariance import portfolio_volatility
from auto_alpha.portfolio.risk.model_covariance import tracking_error
from auto_alpha.portfolio.risk.model_attribution import attribute_active_return
from auto_alpha.portfolio.risk.model_attribution import attribute_portfolio_return
from auto_alpha.portfolio.risk.model_attribution import brinson_industry_attribution
from auto_alpha.portfolio.risk.model_decomposition import active_risk_decomposition
from auto_alpha.portfolio.risk.model_decomposition import factor_risk_contribution
from auto_alpha.portfolio.risk.model_decomposition import portfolio_factor_exposure
from auto_alpha.portfolio.risk.model_decomposition import portfolio_risk_decomposition
from auto_alpha.portfolio.risk.model_decomposition import specific_risk_contribution
from auto_alpha.portfolio.risk.model_exposures import active_exposure
from auto_alpha.portfolio.risk.model_exposures import benchmark_exposure
from auto_alpha.portfolio.risk.model_exposures import benchmark_weights_from_index_members
from auto_alpha.portfolio.risk.model_exposures import build_security_exposures
from auto_alpha.portfolio.risk.model_exposures import portfolio_exposure
from auto_alpha.portfolio.risk.model_factor_model import build_barra_like_risk_model
from auto_alpha.portfolio.risk.model_factor_model import estimate_factor_covariance
from auto_alpha.portfolio.risk.model_factor_model import estimate_factor_returns
from auto_alpha.portfolio.risk.model_factor_model import estimate_specific_risk
from auto_alpha.portfolio.risk.model_industry import build_industry_exposures
from auto_alpha.portfolio.risk.model_models import BenchmarkExposure
from auto_alpha.portfolio.risk.model_models import FactorExposureMatrix
from auto_alpha.portfolio.risk.model_models import FactorModelSpec
from auto_alpha.portfolio.risk.model_models import FactorReturnSeries
from auto_alpha.portfolio.risk.model_models import FactorRiskModel
from auto_alpha.portfolio.risk.model_models import PortfolioExposure
from auto_alpha.portfolio.risk.model_models import RiskConstraintConfig
from auto_alpha.portfolio.risk.model_models import RiskMetrics
from auto_alpha.portfolio.risk.model_models import RiskReport
from auto_alpha.portfolio.risk.model_models import SecurityExposure
from auto_alpha.portfolio.risk.model_report import build_risk_model_report
from auto_alpha.portfolio.risk.model_report import build_risk_report
from auto_alpha.portfolio.risk.model_report import write_risk_model_report
from auto_alpha.portfolio.risk.model_report import write_risk_report
from auto_alpha.portfolio.risk.model_style import STYLE_FACTOR_NAMES
from auto_alpha.portfolio.risk.model_style import build_style_exposures

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
