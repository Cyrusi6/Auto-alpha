"""Lifecycle recommendation rules."""

from __future__ import annotations

from .models import FactorHealthCheck, FactorLifecycleDecision, LifecyclePolicy


def make_lifecycle_decision(
    factor_id: str,
    model_version_id: str | None,
    health_checks: list[FactorHealthCheck],
    policy: LifecyclePolicy,
    current_status: str,
) -> FactorLifecycleDecision:
    blockers = [check for check in health_checks if check.severity == "blocker" and not check.passed]
    errors = [check for check in health_checks if check.severity == "error" and not check.passed]
    warnings = [check for check in health_checks if check.severity == "warning" and not check.passed]
    if blockers:
        action = "quarantine" if current_status == "active" else "manual_review"
        severity = "blocker"
    elif errors:
        action = "pause" if current_status == "active" else "manual_review"
        severity = "error"
    elif current_status == "production_candidate":
        action = "approve_for_activation"
        severity = "info"
    elif current_status == "active":
        action = "keep_active"
        severity = "warning" if warnings else "info"
    else:
        action = "manual_review" if warnings else "approve_for_activation"
        severity = "warning" if warnings else "info"
    return FactorLifecycleDecision(
        factor_id=factor_id,
        model_version_id=model_version_id,
        current_status=current_status,
        recommended_action=action,
        severity=severity,
        reasons=[check.message or check.name for check in [*blockers, *errors, *warnings]],
        checks={
            "blockers": float(len(blockers)),
            "errors": float(len(errors)),
            "warnings": float(len(warnings)),
            "require_review_approval_for_activation": policy.require_review_approval_for_activation,
        },
    )
