"""Security, portfolio, and benchmark exposure utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import torch

from .models import BenchmarkExposure, PortfolioExposure, SecurityExposure


def build_security_exposures(loader) -> list[SecurityExposure]:
    log_mkt_cap = _last_vector(loader.raw_data_cache.get("log_mkt_cap"), len(loader.ts_codes))
    target_ret = loader.target_ret.detach().cpu() if loader.target_ret is not None else torch.zeros((len(loader.ts_codes), 1))
    volatility = torch.nan_to_num(target_ret.std(dim=1, unbiased=False), nan=0.0, posinf=0.0, neginf=0.0)
    market_ret = target_ret.mean(dim=0)
    market_var = float(market_ret.var(unbiased=False).item())
    betas: list[float] = []
    for idx in range(len(loader.ts_codes)):
        series = target_ret[idx]
        if market_var <= 1e-12 or series.numel() <= 1:
            betas.append(1.0)
        else:
            cov = float(((series - series.mean()) * (market_ret - market_ret.mean())).mean().item())
            betas.append(cov / max(market_var, 1e-12))

    exposures: list[SecurityExposure] = []
    for idx, ts_code in enumerate(loader.ts_codes):
        metadata = loader.security_metadata.get(ts_code, {})
        exposures.append(
            SecurityExposure(
                ts_code=ts_code,
                industry=str(metadata.get("industry") or "UNKNOWN"),
                log_mkt_cap=float(log_mkt_cap[idx].item()),
                volatility=float(volatility[idx].item()),
                beta=float(betas[idx]),
            )
        )
    return exposures


def portfolio_exposure(weights, loader) -> PortfolioExposure:
    weight_tensor = _to_weight_tensor(weights, len(loader.ts_codes))
    exposures = build_security_exposures(loader)
    industry_weights: dict[str, float] = {}
    size = 0.0
    vol = 0.0
    beta = 0.0
    for idx, exposure in enumerate(exposures):
        weight = float(weight_tensor[idx].item())
        if abs(weight) <= 1e-12:
            continue
        industry_weights[exposure.industry] = industry_weights.get(exposure.industry, 0.0) + weight
        size += weight * exposure.log_mkt_cap
        vol += weight * exposure.volatility
        beta += weight * exposure.beta
    return PortfolioExposure(
        industry_weights={key: float(value) for key, value in sorted(industry_weights.items())},
        size_exposure=float(size),
        volatility_exposure=float(vol),
        beta_exposure=float(beta),
        concentration_hhi=float((weight_tensor**2).sum().item()),
        top_weight=float(weight_tensor.max().item()) if weight_tensor.numel() else 0.0,
        n_positions=int((weight_tensor > 1e-9).sum().item()),
    )


def benchmark_weights_from_index_members(loader, index_code: str, as_of_date: str) -> torch.Tensor:
    path = Path(loader.data_dir) / "index_members" / "records.jsonl"
    weights = torch.zeros(len(loader.ts_codes), dtype=torch.float32)
    if not path.exists():
        return _equal_weight(loader)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected = [
        record
        for record in records
        if record.get("index_code") == index_code and str(record.get("trade_date", "")) <= as_of_date
    ]
    if not selected:
        return _equal_weight(loader)
    latest_date = max(str(record["trade_date"]) for record in selected)
    latest = [record for record in selected if str(record.get("trade_date")) == latest_date]
    code_index = {ts_code: idx for idx, ts_code in enumerate(loader.ts_codes)}
    for record in latest:
        ts_code = str(record.get("ts_code"))
        if ts_code not in code_index:
            continue
        weights[code_index[ts_code]] = max(0.0, float(record.get("weight") or 0.0))
    total = float(weights.sum().item())
    if total <= 1e-12:
        return _equal_weight(loader)
    return weights / total


def active_exposure(portfolio_weights, benchmark_weights, loader) -> PortfolioExposure:
    active_weights = _to_weight_tensor(portfolio_weights, len(loader.ts_codes)) - _to_weight_tensor(
        benchmark_weights,
        len(loader.ts_codes),
    )
    return portfolio_exposure(active_weights, loader)


def benchmark_exposure(index_code: str, as_of_date: str, benchmark_weights, loader) -> BenchmarkExposure:
    weights = _to_weight_tensor(benchmark_weights, len(loader.ts_codes))
    return BenchmarkExposure(
        index_code=index_code,
        as_of_date=as_of_date,
        weights={loader.ts_codes[idx]: float(value) for idx, value in enumerate(weights.tolist()) if abs(value) > 1e-12},
        exposure=portfolio_exposure(weights, loader),
    )


def _last_vector(values: Any, n_stocks: int) -> torch.Tensor:
    if values is None:
        return torch.zeros(n_stocks, dtype=torch.float32)
    tensor = values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)
    if tensor.ndim == 2:
        return tensor[:, -1]
    return tensor


def _to_weight_tensor(weights, n_stocks: int) -> torch.Tensor:
    tensor = weights.detach().cpu().to(dtype=torch.float32) if hasattr(weights, "detach") else torch.tensor(weights, dtype=torch.float32)
    if tensor.numel() != n_stocks:
        raise ValueError("weights length must match loaded securities")
    return torch.nan_to_num(tensor.reshape(n_stocks), nan=0.0, posinf=0.0, neginf=0.0)


def _equal_weight(loader) -> torch.Tensor:
    n = len(loader.ts_codes)
    if n <= 0:
        return torch.zeros(0, dtype=torch.float32)
    return torch.full((n,), 1.0 / n, dtype=torch.float32)
