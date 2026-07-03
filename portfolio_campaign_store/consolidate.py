"""Consolidate portfolio campaign item outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .models import ProductionCandidateBundleRecord
from .registry import LocalPortfolioCampaignStore


def consolidate_portfolio_campaign(store_dir: str | Path) -> dict[str, Any]:
    store = LocalPortfolioCampaignStore(store_dir)
    bundle: list[ProductionCandidateBundleRecord] = []
    activation_queue: list[dict[str, Any]] = []
    for item in store.load_items():
        if item.get("status") != "success":
            continue
        decision = _read_json(item.get("portfolio_certification_decision_path"))
        policy = _read_json(item.get("certified_portfolio_policy_path"))
        if str(decision.get("status")) not in {"certified", "conditional"}:
            continue
        pool_record = (item.get("metadata") or {}).get("certified_factor_pool_record", {}) if isinstance(item.get("metadata"), dict) else {}
        lab_result = (item.get("metadata") or {}).get("portfolio_lab_result", {}) if isinstance(item.get("metadata"), dict) else {}
        lab_summary = lab_result.get("summary", {}) if isinstance(lab_result.get("summary"), dict) else {}
        robustness = _read_json(str(Path(str(item.get("portfolio_lab_output_dir") or "")) / "portfolio_robustness_report.json"))
        portfolio_score = float((robustness.get("selected_policy") or {}).get("selection_score", 0.0) or lab_summary.get("best_selection_score", 0.0) or 0.0)
        scenario_pass_ratio = float((robustness.get("selected_policy") or {}).get("scenario_pass_ratio", 0.0) or 0.0)
        record = ProductionCandidateBundleRecord(
            production_candidate_bundle_id=f"pcb_{len(bundle)+1:04d}_{item.get('factor_id')}",
            factor_id=str(item.get("factor_id")),
            model_version_id=None,
            portfolio_policy_id=str(policy.get("policy_id") or decision.get("portfolio_policy_id") or ""),
            optimizer_policy_model_version_id=None,
            factor_certification_status=str(pool_record.get("certification_status") or ""),
            portfolio_certification_status=str(decision.get("status") or ""),
            validation_score=float(pool_record.get("validation_score", 0.0) or 0.0),
            portfolio_score=portfolio_score,
            scenario_pass_ratio=scenario_pass_ratio,
            readiness_status="pending_activation_review",
            selected_for_activation_review=True,
            reason="portfolio certification passed",
            artifact_refs={
                "portfolio_lab_report_path": str(item.get("portfolio_lab_report_path") or ""),
                "selected_portfolio_policy_path": str(item.get("selected_portfolio_policy_path") or ""),
                "portfolio_certification_decision_path": str(item.get("portfolio_certification_decision_path") or ""),
                "certified_portfolio_policy_path": str(item.get("certified_portfolio_policy_path") or ""),
            },
            metadata={"portfolio_candidate_item": item, "portfolio_certification_decision": decision},
        )
        bundle.append(record)
        activation_queue.append(
            {
                "activation_queue_id": f"opaq_{len(activation_queue)+1:04d}_{item.get('factor_id')}",
                "production_candidate_bundle_id": record.production_candidate_bundle_id,
                "factor_id": record.factor_id,
                "portfolio_policy_id": record.portfolio_policy_id,
                "priority": len(activation_queue) + 1,
                "status": "pending_review",
                "reason": "requires model_registry and factor_lifecycle approval before activation",
                "artifact_refs": record.artifact_refs,
                "metadata": {"readiness_status": record.readiness_status},
            }
        )
    ordered = sorted(bundle, key=lambda row: (row.portfolio_score, row.validation_score), reverse=True)
    store.write_bundle(ordered)
    store.write_activation_queue(activation_queue)
    report = {
        "status": "success",
        "item_count": len(store.load_items()),
        "production_candidate_bundle_count": len(ordered),
        "optimizer_policy_activation_queue_count": len(activation_queue),
        "best_production_candidate_score": max((row.portfolio_score for row in ordered), default=0.0),
    }
    report_path = store.bundle_report_path
    write_json_artifact(report_path, report, "production_candidate_bundle_report", "portfolio_campaign_store")
    return {**report, "paths": store.paths()}


def _read_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))
