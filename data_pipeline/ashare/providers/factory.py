"""Factory for A-share data providers."""

from __future__ import annotations

from ..config import AShareDataConfig
from .base import AShareDataProvider
from .sample import SampleAShareDataProvider
from .tushare import TushareAShareDataProvider


def create_ashare_provider(config: AShareDataConfig) -> AShareDataProvider:
    provider = config.provider.lower()
    if provider in {"sample", "fixture", "mock"}:
        return SampleAShareDataProvider()
    if provider == "tushare":
        return TushareAShareDataProvider()
    raise ValueError(f"Unsupported A-share provider: {config.provider}")
