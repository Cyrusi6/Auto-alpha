"""Decision logic for pre-live Go/No-Go gates."""

from __future__ import annotations

from datetime import datetime

from .models import GoLiveGateDecision, GoLiveGateScorecard, GoLiveGateStatus

FORBIDDEN_STATUSES = {"ready_for_live_trading", "ready_for_real_broker", "ready_for_auto_submit"}


def make_go_live_gate_decision(scorecard: GoLiveGateScorecard) -> GoLiveGateDecision:
    failed = [check for check in scorecard.checks if check.required and check.status != "passed"]
    blockers = [check for check in scorecard.checks if check.severity == "blocker" and check.status != "passed"]
    if blockers:
        status = GoLiveGateStatus.not_ready
    elif failed:
        status = GoLiveGateStatus.insufficient_data if any(check.status == "missing" for check in failed) else GoLiveGateStatus.not_ready
    else:
        status = scorecard.status
    if status in FORBIDDEN_STATUSES:  # pragma: no cover - defensive guard
        status = GoLiveGateStatus.not_ready
    remediation = [check.to_dict() for check in failed]
    return GoLiveGateDecision(
        status=status,
        passed=not failed and not blockers,
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        score=scorecard.score,
        reasons=[check.reason for check in failed or blockers],
        required_remediation=remediation,
        checks=[check.to_dict() for check in scorecard.checks],
        policy=scorecard.policy,
        metadata={
            "blocker_count": len(blockers),
            "failed_required_count": len(failed),
            "real_submit_status_guard": True,
            "real_broker_submit_enabled": False,
        },
    )
