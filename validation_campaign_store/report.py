"""Reports for validation campaign stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .registry import LocalValidationCampaignStore


def write_validation_campaign_report(store: LocalValidationCampaignStore, extra: dict[str, Any] | None = None) -> tuple[Path, Path]:
    campaigns = store.load_campaigns()
    candidates = store.load_candidates()
    shards = store.load_shards()
    results = store.load_results()
    leaderboard = store.load_leaderboard()
    queue = store.load_certification_queue()
    failed_shards = [row for row in shards if str(row.get("status")) in {"failed", "error"}]
    payload = {
        "status": "partial" if failed_shards else ("ready" if leaderboard else "registered"),
        "validation_campaign_count": len(campaigns),
        "candidate_count": len(candidates),
        "shard_count": len(shards),
        "failed_shard_count": len(failed_shards),
        "result_count": len(results),
        "leaderboard_count": len(leaderboard),
        "certification_queue_count": len(queue),
        "best_validation_score": max((float(row.get("validation_score", 0.0) or 0.0) for row in leaderboard), default=0.0),
        "campaigns": campaigns,
        "summary": {
            "validation_leaderboard_empty": len(leaderboard) == 0,
            "certification_queue_empty": len(queue) == 0,
            "validation_blocker_count": sum(int(row.get("blocker_count", 0) or 0) for row in results),
        },
        "paths": store.paths(),
        "extra": extra or {},
    }
    json_path = write_json_artifact(store.report_path, payload, "validation_campaign_store_report", "validation_campaign_store")
    md_path = store.root_dir / "validation_campaign_store_report.md"
    md_path.write_text(_markdown(payload), encoding="utf-8")
    write_json_artifact(
        store.artifact_catalog_path,
        {"validation_campaign_count": len(campaigns), "artifact_count": len(store.paths()), "artifacts": store.paths()},
        "validation_campaign_artifact_catalog",
        "validation_campaign_store",
    )
    store.write_registry()
    return json_path, md_path


def _markdown(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Validation Campaign Store Report",
            "",
            f"- Status: {payload.get('status')}",
            f"- Candidates: {payload.get('candidate_count', 0)}",
            f"- Shards: {payload.get('shard_count', 0)}",
            f"- Results: {payload.get('result_count', 0)}",
            f"- Leaderboard: {payload.get('leaderboard_count', 0)}",
            f"- Certification queue: {payload.get('certification_queue_count', 0)}",
            "",
        ]
    )
