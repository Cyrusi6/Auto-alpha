"""Risk decomposition utilities for factor risk models."""

from __future__ import annotations

from typing import Any

import torch


def portfolio_factor_exposure(weights, risk_model, date_index: int | None = None) -> dict[str, float]:
    weight = _to_weight(weights, len(risk_model.ts_codes))
    exposures = _exposure_at(risk_model, date_index)
    factor_exposure = weight @ exposures
    return {
        name: float(factor_exposure[idx].item())
        for idx, name in enumerate(risk_model.exposure_matrix.factor_names)
    }


def portfolio_risk_decomposition(weights, risk_model, date_index: int | None = None) -> dict[str, Any]:
    weight = _to_weight(weights, len(risk_model.ts_codes))
    exposures = _exposure_at(risk_model, date_index)
    factor_exposure = weight @ exposures
    cov = _to_tensor(risk_model.factor_covariance)
    specific = _to_tensor(risk_model.specific_risk)
    factor_var = float((factor_exposure @ cov @ factor_exposure).item())
    specific_var = float(((weight * specific) ** 2).sum().item())
    total_var = max(factor_var + specific_var, 0.0)
    total_risk = total_var**0.5
    marginal = cov @ factor_exposure
    raw_contrib = factor_exposure * marginal
    denom = float(raw_contrib.abs().sum().item()) or 1.0
    factor_contrib = {
        name: float(raw_contrib[idx].item() / denom * max(factor_var, 0.0) ** 0.5)
        for idx, name in enumerate(risk_model.exposure_matrix.factor_names)
    }
    style_names = set(risk_model.exposure_matrix.style_factor_names)
    industry_names = set(risk_model.exposure_matrix.industry_factor_names)
    return {
        "total_risk": float(total_risk),
        "factor_risk": float(max(factor_var, 0.0) ** 0.5),
        "specific_risk": float(max(specific_var, 0.0) ** 0.5),
        "factor_contributions": factor_contrib,
        "style_contributions": {k: v for k, v in factor_contrib.items() if k in style_names},
        "industry_contributions": {k: v for k, v in factor_contrib.items() if k in industry_names},
        "active_factor_exposure": portfolio_factor_exposure(weight, risk_model, date_index),
        "factor_risk_share": float(factor_var / total_var) if total_var > 1e-12 else 0.0,
        "specific_risk_share": float(specific_var / total_var) if total_var > 1e-12 else 0.0,
    }


def active_risk_decomposition(weights, benchmark_weights, risk_model, date_index: int | None = None) -> dict[str, Any]:
    active = _to_weight(weights, len(risk_model.ts_codes)) - _to_weight(benchmark_weights, len(risk_model.ts_codes))
    return portfolio_risk_decomposition(active, risk_model, date_index)


def factor_risk_contribution(weights, risk_model, date_index: int | None = None) -> dict[str, float]:
    return portfolio_risk_decomposition(weights, risk_model, date_index)["factor_contributions"]


def specific_risk_contribution(weights, risk_model) -> dict[str, float]:
    weight = _to_weight(weights, len(risk_model.ts_codes))
    specific = _to_tensor(risk_model.specific_risk)
    values = (weight * specific).square()
    total = float(values.sum().item()) or 1.0
    return {ts_code: float(values[idx].item() / total) for idx, ts_code in enumerate(risk_model.ts_codes)}


def _exposure_at(risk_model, date_index: int | None) -> torch.Tensor:
    exposures = _to_tensor(risk_model.exposure_matrix.exposures)
    idx = exposures.shape[2] - 1 if date_index is None else max(0, min(int(date_index), exposures.shape[2] - 1))
    return exposures[:, :, idx]


def _to_weight(values, n: int) -> torch.Tensor:
    tensor = values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)
    return torch.nan_to_num(tensor.reshape(n), nan=0.0, posinf=0.0, neginf=0.0)


def _to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)
