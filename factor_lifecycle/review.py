"""Build human-readable model review packages."""

from __future__ import annotations

from factor_store import FactorRecord
from model_registry.models import ModelVersionRecord

from .models import FactorLifecycleDecision, ModelReviewPackage


def build_model_review_package(
    factor_record: FactorRecord,
    model_version: ModelVersionRecord | None,
    health_checks: list[dict],
    decision: FactorLifecycleDecision,
    promotion_decision: dict | None = None,
    lineage_graph_path: str | None = None,
) -> ModelReviewPackage:
    return ModelReviewPackage(
        model_version_id=model_version.model_version_id if model_version else None,
        factor_id=factor_record.factor_id,
        factor_type=factor_record.factor_type or "single",
        lifecycle_status=model_version.lifecycle_status if model_version else factor_record.status,
        formula=list(factor_record.formula),
        parent_factor_ids=list(factor_record.parent_factor_ids or []),
        source_artifacts=dict(model_version.source_artifacts if model_version else {}),
        key_metrics=dict(factor_record.metrics or {}),
        gate_status=factor_record.gate_status,
        gate_reasons=list(factor_record.gate_reasons or []),
        promotion_decision=promotion_decision or {},
        health_checks=health_checks,
        lifecycle_decision=decision.to_dict(),
        reviewer_checklist=[
            {"item": "data_quality_reviewed", "required": True, "checked": False},
            {"item": "risk_and_capacity_reviewed", "required": True, "checked": False},
            {"item": "broker_or_paper_execution_reviewed", "required": False, "checked": False},
            {"item": "rollback_plan_reviewed", "required": True, "checked": False},
        ],
        lineage_graph_path=lineage_graph_path,
    )
