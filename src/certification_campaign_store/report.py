"""Reports for factor certification campaign stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .registry import LocalFactorCertificationCampaignStore


def write_factor_certification_campaign_report(
    store: LocalFactorCertificationCampaignStore,
    extra: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    campaigns = store.load_campaigns()
    items = store.load_items()
    pool = store.load_certified_pool()
    leaderboard = store.load_leaderboard()
    failed = [row for row in items if str(row.get("status")) in {"failed", "error"}]
    payload = {
        "status": "partial" if failed else ("ready" if pool else ("running" if items else "registered")),
        "campaign_count": len(campaigns),
        "item_count": len(items),
        "failed_item_count": len(failed),
        "certified_factor_pool_count": len(pool),
        "leaderboard_count": len(leaderboard),
        "best_certification_score": max((float(row.get("certification_score", 0.0) or 0.0) for row in leaderboard), default=0.0),
        "campaigns": campaigns,
        "summary": {
            "certified_factor_pool_empty": len(pool) == 0,
            "factor_certification_campaign_partial": bool(failed),
        },
        "paths": store.paths(),
        "extra": extra or {},
    }
    json_path = write_json_artifact(store.report_path, payload, "factor_certification_campaign_report", "certification_campaign_store")
    md_path = store.root_dir / "factor_certification_campaign_report.md"
    md_path.write_text(_markdown(payload), encoding="utf-8")
    write_json_artifact(
        store.artifact_catalog_path,
        {"artifact_count": len(store.paths()), "artifacts": store.paths()},
        "factor_certification_campaign_artifact_catalog",
        "certification_campaign_store",
    )
    store.write_registry()
    return json_path, md_path


def _markdown(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Factor Certification Campaign Report",
            "",
            f"- Status: {payload.get('status')}",
            f"- Items: {payload.get('item_count', 0)}",
            f"- Failed items: {payload.get('failed_item_count', 0)}",
            f"- Certified factor pool: {payload.get('certified_factor_pool_count', 0)}",
            f"- Leaderboard: {payload.get('leaderboard_count', 0)}",
            "",
        ]
    )
