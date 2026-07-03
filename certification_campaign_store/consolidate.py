"""Consolidate factor certification campaign outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .models import CertifiedFactorPoolRecord
from .registry import LocalFactorCertificationCampaignStore


def consolidate_factor_certification_campaign(store_dir: str | Path) -> dict[str, Any]:
    store = LocalFactorCertificationCampaignStore(store_dir)
    pool: list[CertifiedFactorPoolRecord] = []
    for item in store.load_items():
        if item.get("status") != "success":
            continue
        decision_path = item.get("decision_path")
        scorecard_path = item.get("scorecard_path")
        decision = _read_json(decision_path)
        scorecard = _read_json(scorecard_path)
        status = str(decision.get("status") or "needs_review")
        blocker_count = int((scorecard.get("summary") or {}).get("blocker_count", 0) or 0)
        if status not in {"certified", "conditional"} or blocker_count > 0:
            continue
        queue_item = (item.get("metadata") or {}).get("queue_item", {}) if isinstance(item.get("metadata"), dict) else {}
        metadata = queue_item.get("metadata", {}) if isinstance(queue_item.get("metadata"), dict) else {}
        validation_artifacts = metadata.get("validation_artifacts", {}) if isinstance(metadata.get("validation_artifacts"), dict) else {}
        certification_artifacts = {
            "decision_path": str(decision_path or ""),
            "scorecard_path": str(scorecard_path or ""),
            "package_path": str(item.get("package_path") or ""),
        }
        score = float(item.get("validation_score", 0.0) or 0.0) + (1.0 if status == "certified" else 0.5)
        pool.append(
            CertifiedFactorPoolRecord(
                certified_factor_pool_id=f"cfp_{len(pool)+1:04d}_{item.get('factor_id')}",
                factor_id=str(item.get("factor_id")),
                formula_hash=str(item.get("formula_hash") or ""),
                certification_status=status,
                validation_score=float(item.get("validation_score", 0.0) or 0.0),
                certification_score=score,
                priority=int(item.get("validation_rank", len(pool) + 1) or len(pool) + 1),
                factor_store_dir=str(queue_item.get("factor_store_dir") or item.get("factor_store_dir") or ""),
                validation_artifacts={key: str(value) for key, value in validation_artifacts.items()},
                certification_artifacts=certification_artifacts,
                selected_for_portfolio_lab=True,
                reason="certification passed",
                metadata={"campaign_item": item, "decision": decision, "scorecard_summary": scorecard.get("summary", {})},
            )
        )
    ordered = sorted(pool, key=lambda row: (row.certification_score, -row.priority), reverse=True)
    store.write_certified_pool(ordered)
    store.write_leaderboard(ordered)
    report = {
        "status": "success",
        "item_count": len(store.load_items()),
        "certified_factor_pool_count": len(ordered),
        "leaderboard_count": len(ordered),
    }
    report_path = store.root_dir / "factor_certification_campaign_consolidation_report.json"
    write_json_artifact(report_path, report, "factor_certification_campaign_consolidation_report", "certification_campaign_store")
    return {**report, "paths": store.paths() | {"factor_certification_campaign_consolidation_report_path": str(report_path)}}


def _read_json(path: str | None) -> dict[str, Any]:
    if not path or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))
