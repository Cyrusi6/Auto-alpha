"""A-share feature engineering."""

from __future__ import annotations

import torch

from .vocab import FEATURE_NAMES


def _finite(x: torch.Tensor) -> torch.Tensor:
    return torch.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)


def _delay(x: torch.Tensor, periods: int) -> torch.Tensor:
    if periods <= 0:
        return _finite(x)
    if periods >= x.shape[1]:
        return torch.zeros_like(x)
    pad = torch.zeros((x.shape[0], periods), dtype=x.dtype, device=x.device)
    return _finite(torch.cat([pad, x[:, :-periods]], dim=1))


def robust_cross_section_zscore(x: torch.Tensor, limit: float = 5.0) -> torch.Tensor:
    clean = _finite(x)
    median = clean.median(dim=0, keepdim=True).values
    centered = clean - median
    scale = torch.abs(centered).median(dim=0, keepdim=True).values
    scale = torch.where(scale < 1e-6, torch.ones_like(scale), scale)
    return torch.clamp(_finite(centered / scale), -limit, limit)


class AShareFeatureEngineer:
    INPUT_DIM = len(FEATURE_NAMES)

    @staticmethod
    def compute_features(raw_dict: dict[str, torch.Tensor], feature_set_manifest=None) -> torch.Tensor:
        if feature_set_manifest is not None:
            from feature_factory.builder import build_feature_tensor

            class _RawLoader:
                raw_data_cache = raw_dict

            tensor, _warnings = build_feature_tensor(_RawLoader(), feature_set_manifest)
            return tensor

        close = raw_dict["close"]
        high = raw_dict["high"]
        low = raw_dict["low"]
        pre_close = raw_dict["pre_close"]
        amount = raw_dict["amount"]
        turnover_rate = raw_dict["turnover_rate"]
        volume_ratio = raw_dict["volume_ratio"]
        pe_ttm = raw_dict["pe_ttm"]
        pb = raw_dict["pb"]
        total_mv = raw_dict["total_mv"]
        roe = raw_dict["roe"]
        revenue_yoy = raw_dict["revenue_yoy"]

        prev_close = _delay(close, 1)
        prev5_close = _delay(close, 5)
        ret_1d = torch.log(torch.clamp(close, min=1e-6) / torch.clamp(prev_close, min=1e-6))
        ret_5d = torch.log(torch.clamp(close, min=1e-6) / torch.clamp(prev5_close, min=1e-6))
        ret_1d[:, 0] = 0.0
        ret_5d[:, : min(5, ret_5d.shape[1])] = 0.0

        features = [
            ret_1d,
            ret_5d,
            (high - low) / torch.clamp(pre_close, min=1e-6),
            turnover_rate,
            volume_ratio,
            torch.log1p(torch.clamp(amount, min=0.0)),
            torch.log1p(torch.clamp(total_mv, min=0.0)),
            pb,
            pe_ttm,
            roe,
            revenue_yoy,
        ]

        normalized = [robust_cross_section_zscore(feature) for feature in features]
        return torch.stack(normalized, dim=1).to(dtype=torch.float32)


FeatureEngineer = AShareFeatureEngineer
