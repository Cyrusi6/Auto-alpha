"""Point-in-time risk model exposures, covariance, constraints, attribution, and reporting."""

from __future__ import annotations

from typing import Any

import torch


def attribute_portfolio_return(weights_prev, returns, factor_exposures, factor_returns, date_index: int | None = None) -> dict[str, Any]:
    weight = _model_attribution_to_tensor(weights_prev)
    ret = _model_attribution_to_tensor(returns).reshape(weight.numel())
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
    active = _model_attribution_to_tensor(portfolio_weights) - _model_attribution_to_tensor(benchmark_weights)
    payload = attribute_portfolio_return(active, returns, factor_exposures, factor_returns, date_index)
    payload["total_active_return"] = payload["total_return"]
    payload["allocation_effect"] = payload["factor_return"]
    payload["selection_effect"] = payload["specific_return"]
    return payload


def brinson_industry_attribution(portfolio_weights, benchmark_weights, returns, industry_codes) -> dict[str, float]:
    p = _model_attribution_to_tensor(portfolio_weights)
    b = _model_attribution_to_tensor(benchmark_weights)
    r = _model_attribution_to_tensor(returns)
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
    values = _model_attribution_to_tensor(factor_exposures.exposures)
    idx = values.shape[2] - 1 if date_index is None else max(0, min(int(date_index), values.shape[2] - 1))
    return values[:, :, idx]


def _factor_returns(factor_returns, date_index: int | None) -> torch.Tensor:
    values = _model_attribution_to_tensor(factor_returns.returns)
    idx = values.shape[1] - 1 if date_index is None else max(0, min(int(date_index), values.shape[1] - 1))
    return values[:, idx]


def _model_attribution_to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)

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
    weight_tensor = _model_covariance_to_tensor(weights)
    cov_tensor = _model_covariance_to_tensor(cov)
    variance = float((weight_tensor @ cov_tensor @ weight_tensor).item())
    return float(max(variance, 0.0) ** 0.5)


def tracking_error(weights, benchmark_weights, cov) -> float:
    active = _model_covariance_to_tensor(weights) - _model_covariance_to_tensor(benchmark_weights)
    return portfolio_volatility(active, cov)


def _model_covariance_to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)

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
    cov = _model_decomposition_to_tensor(risk_model.factor_covariance)
    specific = _model_decomposition_to_tensor(risk_model.specific_risk)
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
    specific = _model_decomposition_to_tensor(risk_model.specific_risk)
    values = (weight * specific).square()
    total = float(values.sum().item()) or 1.0
    return {ts_code: float(values[idx].item() / total) for idx, ts_code in enumerate(risk_model.ts_codes)}


def _exposure_at(risk_model, date_index: int | None) -> torch.Tensor:
    exposures = _model_decomposition_to_tensor(risk_model.exposure_matrix.exposures)
    idx = exposures.shape[2] - 1 if date_index is None else max(0, min(int(date_index), exposures.shape[2] - 1))
    return exposures[:, :, idx]


def _to_weight(values, n: int) -> torch.Tensor:
    tensor = values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)
    return torch.nan_to_num(tensor.reshape(n), nan=0.0, posinf=0.0, neginf=0.0)


def _model_decomposition_to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)

import torch


def build_industry_exposures(loader) -> tuple[torch.Tensor, list[str], torch.Tensor]:
    industries = [
        str(loader.security_metadata.get(ts_code, {}).get("industry") or "UNKNOWN")
        for ts_code in loader.ts_codes
    ]
    industry_names = sorted(set(industries)) or ["UNKNOWN"]
    mapping = {name: idx for idx, name in enumerate(industry_names)}
    codes = torch.tensor([mapping[name] for name in industries], dtype=torch.long)
    one_hot = torch.zeros((len(loader.ts_codes), len(industry_names)), dtype=torch.float32)
    if len(loader.ts_codes) > 0:
        one_hot[torch.arange(len(loader.ts_codes)), codes] = 1.0
    return one_hot, industry_names, codes

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SecurityExposure:
    ts_code: str
    industry: str
    log_mkt_cap: float
    volatility: float
    beta: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioExposure:
    industry_weights: dict[str, float]
    size_exposure: float
    volatility_exposure: float
    beta_exposure: float
    concentration_hhi: float
    top_weight: float
    n_positions: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkExposure:
    index_code: str
    as_of_date: str
    weights: dict[str, float]
    exposure: PortfolioExposure

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskConstraintConfig:
    max_weight: float = 0.10
    max_industry_active_weight: float = 0.20
    max_total_active_weight: float = 1.00
    max_tracking_error: float = 1.00
    max_turnover: float = 1.00
    min_names: int = 1
    max_names: int = 100
    max_hhi: float = 1.00


@dataclass(frozen=True)
class RiskMetrics:
    portfolio_volatility: float
    tracking_error: float
    active_share: float
    hhi: float
    top_weight: float
    n_positions: float
    industry_active_max: float
    total_active_weight: float
    turnover: float = 0.0
    violations: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {key: float(value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class RiskReport:
    factor_id: str | None
    index_code: str
    as_of_date: str
    portfolio: PortfolioExposure
    benchmark: BenchmarkExposure
    active: PortfolioExposure
    metrics: RiskMetrics
    violations: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    style_exposures: dict[str, float] | None = None
    active_style_exposures: dict[str, float] | None = None
    industry_exposures: dict[str, float] | None = None
    factor_covariance_summary: dict[str, float] | None = None
    specific_risk_summary: dict[str, float] | None = None
    factor_risk_contribution: dict[str, Any] | None = None
    active_risk_contribution: dict[str, Any] | None = None
    attribution_summary: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "index_code": self.index_code,
            "as_of_date": self.as_of_date,
            "portfolio": self.portfolio.to_dict(),
            "benchmark": self.benchmark.to_dict(),
            "active": self.active.to_dict(),
            "metrics": self.metrics.to_dict(),
            "violations": list(self.violations),
            "checks": self.checks,
            "style_exposures": self.style_exposures or {},
            "active_style_exposures": self.active_style_exposures or {},
            "industry_exposures": self.industry_exposures or {},
            "factor_covariance_summary": self.factor_covariance_summary or {},
            "specific_risk_summary": self.specific_risk_summary or {},
            "factor_risk_contribution": self.factor_risk_contribution or {},
            "active_risk_contribution": self.active_risk_contribution or {},
            "attribution_summary": self.attribution_summary or {},
        }


@dataclass(frozen=True)
class FactorModelSpec:
    style_factors: list[str]
    industry_factors: list[str]
    shrinkage: float = 0.1
    lookback: int | None = None

    @property
    def factor_names(self) -> list[str]:
        return [*self.style_factors, *self.industry_factors]


@dataclass(frozen=True)
class FactorExposureMatrix:
    factor_names: list[str]
    style_factor_names: list[str]
    industry_factor_names: list[str]
    exposures: Any


@dataclass(frozen=True)
class FactorReturnSeries:
    factor_names: list[str]
    trade_dates: list[str]
    returns: Any


@dataclass(frozen=True)
class FactorRiskModel:
    spec: FactorModelSpec
    exposure_matrix: FactorExposureMatrix
    factor_returns: FactorReturnSeries
    factor_covariance: Any
    specific_risk: Any
    ts_codes: list[str]
    trade_dates: list[str]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": asdict(self.spec),
            "factor_names": list(self.exposure_matrix.factor_names),
            "style_factor_names": list(self.exposure_matrix.style_factor_names),
            "industry_factor_names": list(self.exposure_matrix.industry_factor_names),
            "ts_codes": list(self.ts_codes),
            "trade_dates": list(self.trade_dates),
            "summary": self.summary,
        }

import torch


STYLE_FACTOR_NAMES = ("size", "value", "momentum", "volatility", "liquidity", "quality", "growth")


def build_style_exposures(loader) -> dict[str, torch.Tensor]:
    raw = loader.raw_data_cache
    close = _field(raw, "adjusted_close", "close")
    ret_5d = _rolling_return(close, 5)
    returns = torch.nan_to_num(loader.target_ret.detach().cpu().to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)
    exposures = {
        "size": _cs_process(raw.get("log_mkt_cap", torch.log1p(torch.clamp(raw["total_mv"], min=0.0)))),
        "value": _cs_process(-0.5 * raw.get("pb", torch.zeros_like(close)) - 0.5 * raw.get("pe_ttm", torch.zeros_like(close))),
        "momentum": _cs_process(ret_5d),
        "volatility": _cs_process(_rolling_std(returns, 5)),
        "liquidity": _cs_process(torch.log1p(torch.clamp(raw.get("amount", torch.zeros_like(close)), min=0.0)) + raw.get("turnover_rate", torch.zeros_like(close))),
        "quality": _cs_process(raw.get("roe", torch.zeros_like(close))),
        "growth": _cs_process(raw.get("revenue_yoy", torch.zeros_like(close))),
    }
    return {name: torch.nan_to_num(value.to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0) for name, value in exposures.items()}


def _field(raw: dict[str, torch.Tensor], preferred: str, fallback: str) -> torch.Tensor:
    return raw.get(preferred, raw[fallback]).detach().cpu().to(dtype=torch.float32)


def _cs_process(x: torch.Tensor) -> torch.Tensor:
    clean = torch.nan_to_num(x.detach().cpu().to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)
    median = clean.median(dim=0, keepdim=True).values
    centered = clean - median
    mad = centered.abs().median(dim=0, keepdim=True).values
    scale = torch.where(mad < 1e-6, torch.ones_like(mad), mad)
    winsorized = torch.clamp(centered / scale, -5.0, 5.0)
    mean = winsorized.mean(dim=0, keepdim=True)
    std = winsorized.std(dim=0, keepdim=True, unbiased=False)
    return torch.nan_to_num((winsorized - mean) / torch.clamp(std, min=1e-6), nan=0.0, posinf=0.0, neginf=0.0)


def _rolling_return(close: torch.Tensor, window: int) -> torch.Tensor:
    clean = torch.clamp(close.detach().cpu().to(dtype=torch.float32), min=1e-6)
    result = torch.zeros_like(clean)
    if clean.shape[1] > window:
        result[:, window:] = torch.log(clean[:, window:] / clean[:, :-window])
    return torch.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)


def _rolling_std(x: torch.Tensor, window: int) -> torch.Tensor:
    if x.shape[1] <= 1:
        return torch.zeros_like(x)
    pad = torch.zeros((x.shape[0], max(window - 1, 0)), dtype=x.dtype)
    windows = torch.cat([pad, x], dim=1).unfold(1, window, 1)
    return torch.nan_to_num(windows.std(dim=-1, unbiased=False), nan=0.0, posinf=0.0, neginf=0.0)

import json
from pathlib import Path
from typing import Any

import torch



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

import numpy as np
import torch



def estimate_factor_returns(loader, factor_exposures: FactorExposureMatrix, ridge: float = 1e-4) -> FactorReturnSeries:
    exposures = _model_factor_model_to_tensor(factor_exposures.exposures)
    returns = _model_factor_model_to_tensor(loader.target_ret)
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
    values = _model_factor_model_to_tensor(factor_returns.returns)
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
    exposures = _model_factor_model_to_tensor(factor_exposures.exposures)
    returns = _model_factor_model_to_tensor(loader.target_ret)
    f_ret = _model_factor_model_to_tensor(factor_returns.returns)
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


def _model_factor_model_to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)

import torch



def check_risk_constraints(weights, benchmark_weights, loader, config: RiskConstraintConfig) -> tuple[bool, list[str], dict[str, object]]:
    weight_tensor = _model_constraints_to_tensor(weights)
    benchmark = _model_constraints_to_tensor(benchmark_weights)
    portfolio = portfolio_exposure(weight_tensor, loader)
    active = active_exposure(weight_tensor, benchmark, loader)
    cov = estimate_return_covariance(loader)
    te = tracking_error(weight_tensor, benchmark, cov)
    active_weight = float(torch.abs(weight_tensor - benchmark).sum().item())
    industry_active = max((abs(value) for value in active.industry_weights.values()), default=0.0)
    violations: list[str] = []
    if portfolio.top_weight > config.max_weight + 1e-6:
        violations.append("max_weight")
    if portfolio.n_positions < config.min_names:
        violations.append("min_names")
    if portfolio.n_positions > config.max_names:
        violations.append("max_names")
    if portfolio.concentration_hhi > config.max_hhi + 1e-9:
        violations.append("max_hhi")
    if industry_active > config.max_industry_active_weight + 1e-9:
        violations.append("max_industry_active_weight")
    if active_weight > config.max_total_active_weight + 1e-9:
        violations.append("max_total_active_weight")
    if te > config.max_tracking_error + 1e-9:
        violations.append("max_tracking_error")
    checks = {
        "top_weight": float(portfolio.top_weight),
        "n_positions": float(portfolio.n_positions),
        "hhi": float(portfolio.concentration_hhi),
        "industry_active_max": float(industry_active),
        "total_active_weight": float(active_weight),
        "tracking_error": float(te),
        "violations": list(violations),
    }
    return not violations, violations, checks


def _model_constraints_to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)

import json
from pathlib import Path
from typing import Any

import torch



def build_risk_report(
    weights,
    benchmark_weights,
    loader,
    index_code: str,
    as_of_date: str,
    factor_id: str | None = None,
    config: RiskConstraintConfig | None = None,
    covariance=None,
    turnover: float = 0.0,
    factor_risk_model=None,
    attribution_summary: dict[str, Any] | None = None,
) -> RiskReport:
    config = config or RiskConstraintConfig()
    cov = covariance if covariance is not None else None
    if cov is None:
        from auto_alpha.portfolio.risk.model import estimate_return_covariance

        cov = estimate_return_covariance(loader)
    weight_tensor = _model_report_to_tensor(weights)
    benchmark = _model_report_to_tensor(benchmark_weights)
    portfolio = portfolio_exposure(weight_tensor, loader)
    benchmark_data = benchmark_exposure(index_code, as_of_date, benchmark, loader)
    active = active_exposure(weight_tensor, benchmark, loader)
    passed, violations, checks = check_risk_constraints(weight_tensor, benchmark, loader, config)
    active_weight = float(torch.abs(weight_tensor - benchmark).sum().item())
    metrics = RiskMetrics(
        portfolio_volatility=portfolio_volatility(weight_tensor, cov),
        tracking_error=tracking_error(weight_tensor, benchmark, cov),
        active_share=0.5 * active_weight,
        hhi=portfolio.concentration_hhi,
        top_weight=portfolio.top_weight,
        n_positions=float(portfolio.n_positions),
        industry_active_max=float(max((abs(value) for value in active.industry_weights.values()), default=0.0)),
        total_active_weight=active_weight,
        turnover=float(turnover),
        violations=float(len(violations)),
    )
    style_exposures = None
    active_style_exposures = None
    factor_risk_contribution = None
    active_risk_contribution = None
    factor_covariance_summary = None
    specific_risk_summary = None
    if factor_risk_model is not None:
        date_idx = loader.trade_dates.index(as_of_date) if as_of_date in loader.trade_dates else len(loader.trade_dates) - 1
        exposure = portfolio_factor_exposure(weight_tensor, factor_risk_model, date_idx)
        active_factor_exposure = portfolio_factor_exposure(weight_tensor - benchmark, factor_risk_model, date_idx)
        style_names = set(factor_risk_model.exposure_matrix.style_factor_names)
        industry_names = set(factor_risk_model.exposure_matrix.industry_factor_names)
        style_exposures = {name: float(exposure.get(name, 0.0)) for name in sorted(style_names)}
        active_style_exposures = {name: float(active_factor_exposure.get(name, 0.0)) for name in sorted(style_names)}
        factor_risk_contribution = portfolio_risk_decomposition(weight_tensor, factor_risk_model, date_idx)
        active_risk_contribution = active_risk_decomposition(weight_tensor, benchmark, factor_risk_model, date_idx)
        factor_cov = _model_report_to_tensor(factor_risk_model.factor_covariance)
        specific = _model_report_to_tensor(factor_risk_model.specific_risk)
        factor_covariance_summary = {
            "factor_count": float(factor_cov.shape[0]),
            "trace": float(torch.trace(factor_cov).item()),
            "max_diag": float(torch.diag(factor_cov).max().item()) if factor_cov.numel() else 0.0,
        }
        specific_risk_summary = {
            "mean": float(specific.mean().item()) if specific.numel() else 0.0,
            "max": float(specific.max().item()) if specific.numel() else 0.0,
        }
        checks = {
            **checks,
            "max_style_exposure_abs": max((abs(value) for value in style_exposures.values()), default=0.0),
            "max_active_style_exposure_abs": max((abs(value) for value in active_style_exposures.values()), default=0.0),
            "factor_risk_share": float(factor_risk_contribution.get("factor_risk_share", 0.0)),
            "specific_risk_share": float(factor_risk_contribution.get("specific_risk_share", 0.0)),
        }
    return RiskReport(
        factor_id=factor_id,
        index_code=index_code,
        as_of_date=as_of_date,
        portfolio=portfolio,
        benchmark=benchmark_data,
        active=active,
        metrics=metrics,
        violations=violations,
        checks={**checks, "passed": passed},
        style_exposures=style_exposures,
        active_style_exposures=active_style_exposures,
        industry_exposures={name: float(active.industry_weights.get(name, 0.0)) for name in active.industry_weights},
        factor_covariance_summary=factor_covariance_summary,
        specific_risk_summary=specific_risk_summary,
        factor_risk_contribution=factor_risk_contribution,
        active_risk_contribution=active_risk_contribution,
        attribution_summary=attribution_summary,
    )


def write_risk_report(report: RiskReport, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "risk_report.json"
    md_path = root / "risk_report.md"
    payload = report.to_dict()
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    return json_path, md_path


def build_risk_model_report(
    weights,
    benchmark_weights,
    loader,
    index_code: str,
    as_of_date: str,
    factor_id: str | None = None,
    lookback: int | None = None,
    shrinkage: float = 0.1,
    attribution_summary: dict[str, Any] | None = None,
) -> RiskReport:
    risk_model = build_barra_like_risk_model(loader, lookback=lookback, shrinkage=shrinkage)
    return build_risk_report(
        weights,
        benchmark_weights,
        loader,
        index_code,
        as_of_date,
        factor_id=factor_id,
        factor_risk_model=risk_model,
        attribution_summary=attribution_summary,
    )


def write_risk_model_report(report: RiskReport, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "risk_model_report.json"
    md_path = root / "risk_model_report.md"
    payload = report.to_dict()
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    return json_path, md_path


def _render_markdown(payload: dict[str, Any]) -> str:
    metrics = payload.get("metrics", {})
    violations = payload.get("violations", [])
    lines = [
        "# Risk Report",
        "",
        f"- factor_id: `{payload.get('factor_id')}`",
        f"- index_code: `{payload.get('index_code')}`",
        f"- as_of_date: `{payload.get('as_of_date')}`",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key, value in sorted(metrics.items()):
        lines.append(f"| {key} | {float(value):.6f} |")
    lines.extend(["", "## Violations", ""])
    if violations:
        lines.extend(f"- {item}" for item in violations)
    else:
        lines.append("- none")
    lines.extend(["", "## Industry Weights", "", "| Industry | Portfolio | Benchmark | Active |", "| --- | ---: | ---: | ---: |"])
    portfolio = payload.get("portfolio", {}).get("industry_weights", {})
    benchmark = payload.get("benchmark", {}).get("exposure", {}).get("industry_weights", {})
    active = payload.get("active", {}).get("industry_weights", {})
    for industry in sorted(set(portfolio) | set(benchmark) | set(active)):
        lines.append(
            f"| {industry} | {float(portfolio.get(industry, 0.0)):.6f} | "
            f"{float(benchmark.get(industry, 0.0)):.6f} | {float(active.get(industry, 0.0)):.6f} |"
        )
    style = payload.get("style_exposures", {})
    active_style = payload.get("active_style_exposures", {})
    if style or active_style:
        lines.extend(["", "## Style Exposures", "", "| Style | Portfolio | Active |", "| --- | ---: | ---: |"])
        for name in sorted(set(style) | set(active_style)):
            lines.append(f"| {name} | {float(style.get(name, 0.0)):.6f} | {float(active_style.get(name, 0.0)):.6f} |")
    risk = payload.get("factor_risk_contribution", {})
    if risk:
        lines.extend(
            [
                "",
                "## Risk Decomposition",
                "",
                f"- total_risk: `{float(risk.get('total_risk', 0.0)):.6f}`",
                f"- factor_risk: `{float(risk.get('factor_risk', 0.0)):.6f}`",
                f"- specific_risk: `{float(risk.get('specific_risk', 0.0)):.6f}`",
            ]
        )
    attribution = payload.get("attribution_summary", {})
    if attribution:
        lines.extend(["", "## Attribution", "", "```json", json.dumps(attribution, ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines) + "\n"


def _model_report_to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)

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
