"""Barra-like multi-factor risk model v1."""

from __future__ import annotations

import numpy as np
import torch

from .industry import build_industry_exposures
from .models import FactorExposureMatrix, FactorModelSpec, FactorReturnSeries, FactorRiskModel
from .style import STYLE_FACTOR_NAMES, build_style_exposures


def estimate_factor_returns(loader, factor_exposures: FactorExposureMatrix, ridge: float = 1e-4) -> FactorReturnSeries:
    exposures = _to_tensor(factor_exposures.exposures)
    returns = _to_tensor(loader.target_ret)
    n_factors = exposures.shape[1]
    n_dates = exposures.shape[2]
    factor_returns = torch.zeros((n_factors, n_dates), dtype=torch.float32)
    for date_idx in range(n_dates):
        x = exposures[:, :, date_idx].numpy()
        y = returns[:, date_idx].numpy()
        finite = np.isfinite(x).all(axis=1) & np.isfinite(y)
        if finite.sum() < 1:
            continue
        x_sel = x[finite]
        y_sel = y[finite]
        try:
            xtx = x_sel.T @ x_sel + np.eye(n_factors, dtype=np.float32) * float(ridge)
            beta = np.linalg.pinv(xtx) @ x_sel.T @ y_sel
        except np.linalg.LinAlgError:
            beta = np.zeros(n_factors, dtype=np.float32)
        factor_returns[:, date_idx] = torch.tensor(np.nan_to_num(beta), dtype=torch.float32)
    return FactorReturnSeries(
        factor_names=factor_exposures.factor_names,
        trade_dates=list(loader.trade_dates),
        returns=factor_returns,
    )


def estimate_factor_covariance(factor_returns: FactorReturnSeries, shrinkage: float = 0.1) -> torch.Tensor:
    values = _to_tensor(factor_returns.returns)
    n_factors = values.shape[0]
    if values.shape[1] <= 1:
        return torch.eye(n_factors, dtype=torch.float32) * 1e-6
    centered = values - values.mean(dim=1, keepdim=True)
    cov = centered @ centered.T / max(1, values.shape[1] - 1)
    diag = torch.diag(torch.clamp(torch.diag(cov), min=1e-10))
    cov = (1.0 - float(shrinkage)) * cov + float(shrinkage) * diag
    cov = torch.nan_to_num((cov + cov.T) / 2.0, nan=0.0, posinf=0.0, neginf=0.0)
    return cov + torch.eye(n_factors, dtype=torch.float32) * 1e-10


def estimate_specific_risk(loader, factor_exposures: FactorExposureMatrix, factor_returns: FactorReturnSeries) -> torch.Tensor:
    exposures = _to_tensor(factor_exposures.exposures)
    returns = _to_tensor(loader.target_ret)
    f_ret = _to_tensor(factor_returns.returns)
    fitted = torch.einsum("nft,ft->nt", exposures, f_ret)
    residual = torch.nan_to_num(returns - fitted, nan=0.0, posinf=0.0, neginf=0.0)
    if residual.shape[1] <= 1:
        fallback = returns.std(dim=1, unbiased=False)
        return torch.clamp(torch.nan_to_num(fallback, nan=0.0, posinf=0.0, neginf=0.0), min=1e-6)
    specific = residual.std(dim=1, unbiased=False)
    fallback = returns.std(dim=1, unbiased=False)
    specific = torch.where(specific <= 1e-8, fallback, specific)
    return torch.clamp(torch.nan_to_num(specific, nan=1e-6, posinf=1e-6, neginf=1e-6), min=1e-6)


def build_barra_like_risk_model(loader, lookback: int | None = None, shrinkage: float = 0.1, as_of_index: int | None = None) -> FactorRiskModel:
    style = build_style_exposures(loader)
    industry, industry_names, _ = build_industry_exposures(loader)
    n_dates = len(loader.trade_dates)
    industry_cube = industry.unsqueeze(2).expand(-1, -1, n_dates)
    style_names = list(STYLE_FACTOR_NAMES)
    style_cube = torch.stack([style[name] for name in style_names], dim=1)
    exposures = torch.cat([style_cube, industry_cube], dim=1)
    factor_names = [*style_names, *industry_names]
    estimation_end = n_dates if as_of_index is None else max(0, min(int(as_of_index), n_dates))
    estimation_start = 0 if lookback is None or lookback <= 0 else max(0, estimation_end - int(lookback))
    if estimation_end > estimation_start:
        exposures_for_estimation = exposures[:, :, estimation_start:estimation_end]
        original_dates = list(loader.trade_dates)
        original_target = loader.target_ret
        class _WindowLoader:
            pass
        estimation_loader = _WindowLoader()
        estimation_loader.target_ret = original_target[:, estimation_start:estimation_end]
        estimation_loader.trade_dates = original_dates[estimation_start:estimation_end]
    else:
        exposures_for_estimation = exposures[:, :, :0]
        class _EmptyLoader:
            pass
        estimation_loader = _EmptyLoader()
        estimation_loader.target_ret = loader.target_ret[:, :0]
        estimation_loader.trade_dates = []
    exposure_matrix = FactorExposureMatrix(
        factor_names=factor_names,
        style_factor_names=style_names,
        industry_factor_names=industry_names,
        exposures=exposures_for_estimation,
    )
    factor_returns = estimate_factor_returns(estimation_loader, exposure_matrix)
    factor_covariance = estimate_factor_covariance(factor_returns, shrinkage=shrinkage)
    specific_risk = estimate_specific_risk(estimation_loader, exposure_matrix, factor_returns)
    full_exposure_matrix = FactorExposureMatrix(
        factor_names=factor_names,
        style_factor_names=style_names,
        industry_factor_names=industry_names,
        exposures=exposures,
    )
    summary = {
        "n_stocks": len(loader.ts_codes),
        "n_dates": len(loader.trade_dates),
        "n_factors": len(factor_names),
        "style_factors": len(style_names),
        "industry_factors": len(industry_names),
        "factor_covariance_trace": float(torch.trace(factor_covariance).item()),
        "specific_risk_mean": float(specific_risk.mean().item()) if specific_risk.numel() else 0.0,
        "specific_risk_max": float(specific_risk.max().item()) if specific_risk.numel() else 0.0,
        "estimation_start_index": estimation_start,
        "estimation_end_index_exclusive": estimation_end,
        "point_in_time": as_of_index is not None,
    }
    return FactorRiskModel(
        spec=FactorModelSpec(style_factors=style_names, industry_factors=industry_names, shrinkage=shrinkage, lookback=lookback),
        exposure_matrix=full_exposure_matrix,
        factor_returns=factor_returns,
        factor_covariance=factor_covariance,
        specific_risk=specific_risk,
        ts_codes=list(loader.ts_codes),
        trade_dates=list(loader.trade_dates),
        summary=summary,
    )


def _to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)
