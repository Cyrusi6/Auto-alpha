"""Reports for portfolio campaign stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .registry import LocalPortfolioCampaignStore


def write_portfolio_campaign_report(store: LocalPortfolioCampaignStore, extra: dict[str, Any] | None = None) -> tuple[Path, Path]:
    campaigns = store.load_campaigns()
    items = store.load_items()
    bundle = store.load_bundle()
    queue = store.load_activation_queue()
    failed = [row for row in items if str(row.get("status")) in {"failed", "error"}]
    payload = {
        "status": "partial" if failed else ("ready" if bundle else ("running" if items else "registered")),
        "campaign_count": len(campaigns),
        "item_count": len(items),
        "failed_item_count": len(failed),
        "production_candidate_bundle_count": len(bundle),
        "optimizer_policy_activation_queue_count": len(queue),
        "best_production_candidate_score": max((float(row.get("portfolio_score", 0.0) or 0.0) for row in bundle), default=0.0),
        "campaigns": campaigns,
        "summary": {
            "production_candidate_bundle_empty": len(bundle) == 0,
            "optimizer_policy_activation_queue_pending": len(queue),
        },
        "paths": store.paths(),
        "extra": extra or {},
    }
    json_path = write_json_artifact(store.report_path, payload, "portfolio_certification_campaign_report", "portfolio_campaign_store")
    md_path = store.root_dir / "portfolio_certification_campaign_report.md"
    md_path.write_text(_markdown(payload), encoding="utf-8")
    write_json_artifact(store.artifact_catalog_path, {"artifact_count": len(store.paths()), "artifacts": store.paths()}, "portfolio_campaign_artifact_catalog", "portfolio_campaign_store")
    store.write_registry()
    return json_path, md_path


def _markdown(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Portfolio Certification Campaign Report",
            "",
            f"- Status: {payload.get('status')}",
            f"- Items: {payload.get('item_count', 0)}",
            f"- Failed items: {payload.get('failed_item_count', 0)}",
            f"- Production candidates: {payload.get('production_candidate_bundle_count', 0)}",
            f"- Activation queue: {payload.get('optimizer_policy_activation_queue_count', 0)}",
            "",
        ]
    )
