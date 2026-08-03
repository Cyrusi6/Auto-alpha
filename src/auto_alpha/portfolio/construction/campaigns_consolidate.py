"""Consolidate portfolio campaign item outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact

from auto_alpha.portfolio.construction.campaigns_registry import LocalPortfolioCampaignStore


def consolidate_portfolio_campaign(store_dir: str | Path) -> dict[str, Any]:
    store = LocalPortfolioCampaignStore(store_dir)
    bundle: list[Any] = []
    activation_queue: list[dict[str, Any]] = []
    ordered = sorted(bundle, key=lambda row: (row.portfolio_score, row.validation_score), reverse=True)
    store.write_bundle(ordered)
    store.write_activation_queue(activation_queue)
    report = {
        "status": "success",
        "item_count": len(store.load_items()),
        "production_candidate_bundle_count": len(ordered),
        "optimizer_policy_activation_queue_count": len(activation_queue),
        "best_production_candidate_score": max((row.portfolio_score for row in ordered), default=0.0),
        "legacy_portfolio_campaign_superseded_by": "portfolio_research",
        "shadow_only": True,
    }
    report_path = store.bundle_report_path
    write_json_artifact(report_path, report, "production_candidate_bundle_report", "portfolio_campaign_store")
    return {**report, "paths": store.paths()}
