"""Readiness decision logic."""

from __future__ import annotations

from datetime import datetime

from .models import LiveReadinessDecision, LiveReadinessScorecard, LiveReadinessStatus


def make_live_readiness_decision(scorecard: LiveReadinessScorecard) -> LiveReadinessDecision:
    failed = [check for check in scorecard.checks if check.required and check.status != "passed"]
    warnings = [check for check in scorecard.checks if not check.required and check.status != "passed"]
    if failed:
        insufficient_ids = {"replay_day_count", "shadow_day_count"}
        status = LiveReadinessStatus.insufficient_data if any(check.check_id in insufficient_ids for check in failed) else LiveReadinessStatus.not_ready
        new_status = status
    elif warnings:
        status = scorecard.status
        new_status = f"{status}_with_remediation"
    else:
        status = scorecard.status
        new_status = status
    return LiveReadinessDecision(
        status=status,
        passed=not failed,
        new_status=new_status,
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        score=scorecard.score,
        reasons=[check.reason for check in failed or warnings],
        required_remediation=[check.to_dict() for check in failed],
        checks=[check.to_dict() for check in scorecard.checks],
        policy=scorecard.policy,
        metadata={"warning_count": len(warnings), "failed_required_count": len(failed)},
    )
