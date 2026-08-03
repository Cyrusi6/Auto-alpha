"""Production candidate bundle helpers."""

from __future__ import annotations

from pathlib import Path

from auto_alpha.portfolio.construction.campaigns_consolidate import consolidate_portfolio_campaign
from auto_alpha.portfolio.construction.campaigns_registry import LocalPortfolioCampaignStore


def build_production_candidate_bundle(store_dir: str | Path) -> list[dict]:
    consolidate_portfolio_campaign(store_dir)
    return LocalPortfolioCampaignStore(store_dir).load_bundle()
