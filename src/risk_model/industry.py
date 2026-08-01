"""Industry exposure construction for A-share risk models."""

from __future__ import annotations

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
