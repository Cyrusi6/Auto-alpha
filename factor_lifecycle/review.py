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
    pit_summary = _summary_from_checks(health_checks, "point_in_time")
    leakage_summary = _summary_from_checks(health_checks, "leakage")
    corporate_action_summary = _summary_from_checks(health_checks, "corporate_action")
    corporate_action_summary["adjustment_reconciliation"] = _summary_from_checks(health_checks, "adjustment_reconciliation")
    corporate_action_summary["total_return"] = _summary_from_checks(health_checks, "total_return")
    settlement_summary = _summary_from_checks(health_checks, "settlement")
    settlement_summary["account_reconciliation"] = _summary_from_checks(health_checks, "account_reconciliation")
    validation_summary = _summary_from_checks(health_checks, "validation")
    validation_summary["overfit"] = _summary_from_checks(health_checks, "overfit")
    validation_summary["placebo"] = _summary_from_checks(health_checks, "placebo")
    certification_summary = _summary_from_checks(health_checks, "certification")
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
            {"item": "point_in_time_and_leakage_reviewed", "required": True, "checked": False},
            {"item": "corporate_actions_and_total_return_reviewed", "required": True, "checked": False},
            {"item": "settlement_nav_and_account_reconciliation_reviewed", "required": True, "checked": False},
            {"item": "validation_lab_and_overfit_reviewed", "required": True, "checked": False},
            {"item": "factor_certification_reviewed", "required": True, "checked": False},
        ],
        pit_summary=pit_summary,
        leakage_summary=leakage_summary,
        corporate_action_summary=corporate_action_summary,
        settlement_summary=settlement_summary,
        validation_summary=validation_summary,
        certification_summary=certification_summary,
        lineage_graph_path=lineage_graph_path,
    )


def _summary_from_checks(checks: list[dict], prefix: str) -> dict:
    selected = [check for check in checks if str(check.get("name", "")).startswith(prefix) or prefix in str(check.get("name", ""))]
    return {
        "checks": selected,
        "passed": all(bool(check.get("passed", True)) for check in selected) if selected else None,
    }
