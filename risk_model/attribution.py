"""Return attribution helpers for factor risk models."""

from __future__ import annotations

from typing import Any

import torch


def attribute_portfolio_return(weights_prev, returns, factor_exposures, factor_returns, date_index: int | None = None) -> dict[str, Any]:
    weight = _to_tensor(weights_prev)
    ret = _to_tensor(returns).reshape(weight.numel())
    exposures = _exposures(factor_exposures, date_index)
    f_ret = _factor_returns(factor_returns, date_index)
    factor_exposure = weight @ exposures
    factor_return = float((factor_exposure * f_ret).sum().item())
    total_return = float((weight * ret).sum().item())
    specific_return = total_return - factor_return
    return {
        "total_return": total_return,
        "factor_return": factor_return,
        "specific_return": specific_return,
        "factor_contributions": {
            name: float(factor_exposure[idx].item() * f_ret[idx].item())
            for idx, name in enumerate(factor_exposures.factor_names)
        },
    }


def attribute_active_return(portfolio_weights, benchmark_weights, returns, factor_exposures, factor_returns, date_index: int | None = None) -> dict[str, Any]:
    active = _to_tensor(portfolio_weights) - _to_tensor(benchmark_weights)
    payload = attribute_portfolio_return(active, returns, factor_exposures, factor_returns, date_index)
    payload["total_active_return"] = payload["total_return"]
    payload["allocation_effect"] = payload["factor_return"]
    payload["selection_effect"] = payload["specific_return"]
    return payload


def brinson_industry_attribution(portfolio_weights, benchmark_weights, returns, industry_codes) -> dict[str, float]:
    p = _to_tensor(portfolio_weights)
    b = _to_tensor(benchmark_weights)
    r = _to_tensor(returns)
    codes = industry_codes.detach().cpu().reshape(-1) if hasattr(industry_codes, "detach") else torch.tensor(industry_codes).reshape(-1)
    allocation = 0.0
    selection = 0.0
    for code in sorted(set(int(item) for item in codes.tolist())):
        mask = codes == code
        p_w = float(p[mask].sum().item())
        b_w = float(b[mask].sum().item())
        p_ret = float((p[mask] * r[mask]).sum().item() / max(p_w, 1e-12)) if p_w > 1e-12 else 0.0
        b_ret = float((b[mask] * r[mask]).sum().item() / max(b_w, 1e-12)) if b_w > 1e-12 else 0.0
        allocation += (p_w - b_w) * b_ret
        selection += p_w * (p_ret - b_ret)
    return {
        "allocation_effect": float(allocation),
        "selection_effect": float(selection),
        "total_active_return": float(((p - b) * r).sum().item()),
    }


def _exposures(factor_exposures, date_index: int | None) -> torch.Tensor:
    values = _to_tensor(factor_exposures.exposures)
    idx = values.shape[2] - 1 if date_index is None else max(0, min(int(date_index), values.shape[2] - 1))
    return values[:, :, idx]


def _factor_returns(factor_returns, date_index: int | None) -> torch.Tensor:
    values = _to_tensor(factor_returns.returns)
    idx = values.shape[1] - 1 if date_index is None else max(0, min(int(date_index), values.shape[1] - 1))
    return values[:, idx]


def _to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)
