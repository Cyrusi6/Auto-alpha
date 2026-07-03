"""Certified factor leaderboard helpers."""

from __future__ import annotations

from pathlib import Path

from .registry import LocalFactorCertificationCampaignStore


def build_certified_factor_leaderboard(store_dir: str | Path, *, top_k: int = 100) -> list[dict]:
    store = LocalFactorCertificationCampaignStore(store_dir)
    pool = sorted(
        store.load_certified_pool(),
        key=lambda row: (float(row.get("certification_score", 0.0) or 0.0), -int(row.get("priority", 0) or 0)),
        reverse=True,
    )[:top_k]
    store.write_leaderboard(pool)
    return store.load_leaderboard()
