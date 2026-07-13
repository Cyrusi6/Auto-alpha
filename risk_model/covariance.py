"""Return covariance and portfolio risk helpers."""

from __future__ import annotations

import torch


def estimate_return_covariance(loader, lookback: int | None = None, shrinkage: float = 0.1, as_of_index: int | None = None) -> torch.Tensor:
    returns = loader.target_ret.detach().cpu().to(dtype=torch.float32)
    if as_of_index is not None:
        end = max(0, min(int(as_of_index), returns.shape[1]))
        returns = returns[:, :end]
    if lookback is not None and lookback > 0:
        returns = returns[:, -lookback:]
    n_stocks = returns.shape[0]
    if returns.shape[1] <= 1:
        return torch.eye(n_stocks, dtype=torch.float32) * 1e-4
    centered = returns - returns.mean(dim=1, keepdim=True)
    cov = centered @ centered.T / max(1, centered.shape[1] - 1)
    diag = torch.diag(torch.clamp(torch.diag(cov), min=1e-8))
    cov = (1.0 - float(shrinkage)) * cov + float(shrinkage) * diag
    cov = torch.nan_to_num(cov, nan=0.0, posinf=0.0, neginf=0.0)
    cov = (cov + cov.T) / 2.0
    return cov + torch.eye(n_stocks, dtype=torch.float32) * 1e-8


def portfolio_volatility(weights, cov) -> float:
    weight_tensor = _to_tensor(weights)
    cov_tensor = _to_tensor(cov)
    variance = float((weight_tensor @ cov_tensor @ weight_tensor).item())
    return float(max(variance, 0.0) ** 0.5)


def tracking_error(weights, benchmark_weights, cov) -> float:
    active = _to_tensor(weights) - _to_tensor(benchmark_weights)
    return portfolio_volatility(active, cov)


def _to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)
