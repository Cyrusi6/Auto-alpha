"""Cross-sectional factor preprocessing for A-share research."""

from __future__ import annotations

import torch


SUPPORTED_TRANSFORMS = {
    "raw",
    "winsorize",
    "zscore",
    "winsorize_zscore",
    "neutralize_market_cap",
    "neutralize_industry",
    "neutralize_industry_size",
}


def cs_winsorize_mad(factors: torch.Tensor, n_mad: float = 5.0) -> torch.Tensor:
    clean = _finite(factors)
    median = clean.median(dim=0, keepdim=True).values
    centered = clean - median
    mad = torch.abs(centered).median(dim=0, keepdim=True).values
    scale = torch.where(mad < 1e-6, torch.ones_like(mad), mad)
    lower = median - n_mad * scale
    upper = median + n_mad * scale
    return _finite(torch.minimum(torch.maximum(clean, lower), upper))


def cs_zscore(factors: torch.Tensor) -> torch.Tensor:
    clean = _finite(factors)
    mean = clean.mean(dim=0, keepdim=True)
    std = clean.std(dim=0, keepdim=True, unbiased=False)
    std = torch.where(std < 1e-6, torch.ones_like(std), std)
    return _finite((clean - mean) / std)


def neutralize_market_cap(factors: torch.Tensor, log_mkt_cap: torch.Tensor) -> torch.Tensor:
    y = _finite(factors)
    x = _finite(_align_matrix(log_mkt_cap, y))
    x_centered = x - x.mean(dim=0, keepdim=True)
    y_centered = y - y.mean(dim=0, keepdim=True)
    denom = (x_centered * x_centered).sum(dim=0, keepdim=True)
    safe_denom = torch.where(denom > 1e-12, denom, torch.ones_like(denom))
    beta_raw = (x_centered * y_centered).sum(dim=0, keepdim=True) / safe_denom
    beta = torch.where(denom > 1e-12, beta_raw, torch.zeros_like(denom))
    residual = y_centered - beta * x_centered
    return _finite(residual)


def neutralize_industry(factors: torch.Tensor, industry_codes: torch.Tensor) -> torch.Tensor:
    clean = _finite(factors)
    codes = _industry_matrix(industry_codes, clean)
    residual = clean.clone()
    valid_codes = torch.unique(codes)
    for code in valid_codes.tolist():
        mask = codes == int(code)
        count = mask.sum(dim=0, keepdim=True).clamp(min=1)
        group_mean = torch.where(mask, clean, torch.zeros_like(clean)).sum(dim=0, keepdim=True) / count
        residual = torch.where(mask, clean - group_mean, residual)
    return _finite(residual)


def neutralize_industry_size(
    factors: torch.Tensor,
    industry_codes: torch.Tensor,
    log_mkt_cap: torch.Tensor,
) -> torch.Tensor:
    industry_residual = neutralize_industry(factors, industry_codes)
    return neutralize_market_cap(industry_residual, log_mkt_cap)


def preprocess_factor(
    factors: torch.Tensor,
    raw_data: dict[str, torch.Tensor],
    method: str,
) -> torch.Tensor:
    method = method.lower()
    if method not in SUPPORTED_TRANSFORMS:
        raise ValueError(f"unsupported factor transform: {method}")

    clean = _finite(factors)
    if method == "raw":
        return clean
    if method == "winsorize":
        return cs_winsorize_mad(clean)
    if method == "zscore":
        return cs_zscore(clean)
    if method == "winsorize_zscore":
        return cs_zscore(cs_winsorize_mad(clean))
    if method == "neutralize_market_cap":
        return neutralize_market_cap(clean, raw_data["log_mkt_cap"])
    if method == "neutralize_industry":
        return neutralize_industry(clean, raw_data["industry_codes"])
    if method == "neutralize_industry_size":
        return neutralize_industry_size(clean, raw_data["industry_codes"], raw_data["log_mkt_cap"])
    return clean


def preprocess_factor_with_validity(
    factors: torch.Tensor,
    validity: torch.Tensor,
    raw_data: dict[str, torch.Tensor],
    method: str,
    eligible_mask: torch.Tensor | None = None,
) -> tuple[torch.Tensor, torch.Tensor]:
    valid = validity.bool() & torch.isfinite(factors)
    if eligible_mask is not None:
        valid &= eligible_mask.bool()
    masked = torch.where(valid, factors, torch.full_like(factors, float("nan")))
    result = torch.zeros_like(factors, dtype=torch.float32)
    for date_index in range(factors.shape[1]):
        date_valid = valid[:, date_index]
        if int(date_valid.sum()) < 2:
            valid[:, date_index] = False
            continue
        date_raw: dict[str, torch.Tensor] = {}
        for key, value in raw_data.items():
            aligned = _align_matrix(value, factors)
            date_raw[key] = aligned[date_valid, date_index : date_index + 1]
        transformed = preprocess_factor(masked[date_valid, date_index : date_index + 1], date_raw, method)
        result[date_valid, date_index] = transformed[:, 0]
    valid &= torch.isfinite(result)
    return torch.where(valid, result, torch.zeros_like(result)), valid


def _finite(x: torch.Tensor) -> torch.Tensor:
    return torch.nan_to_num(x.to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)


def _align_matrix(value: torch.Tensor, reference: torch.Tensor) -> torch.Tensor:
    if value.ndim == 1:
        return value.unsqueeze(1).expand(-1, reference.shape[1])
    return value


def _industry_matrix(industry_codes: torch.Tensor, reference: torch.Tensor) -> torch.Tensor:
    if industry_codes.ndim == 1:
        return industry_codes.to(device=reference.device).long().unsqueeze(1).expand(-1, reference.shape[1])
    return industry_codes.to(device=reference.device).long()
