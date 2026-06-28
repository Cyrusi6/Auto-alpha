"""Decision logic for portfolio certification."""

from __future__ import annotations

from datetime import datetime

from .models import PortfolioCertificationDecision, PortfolioCertificationPolicy, PortfolioCertificationScorecard, PortfolioCertificationStatus


def make_portfolio_certification_decision(
    scorecard: PortfolioCertificationScorecard,
    policy: PortfolioCertificationPolicy,
) -> PortfolioCertificationDecision:
    failed = [check for check in scorecard.checks if check.status == "failed"]
    blockers = [check for check in failed if check.severity == "blocker"]
    errors = [check for check in failed if check.severity == "error"]
    required_missing = [check for check in failed if check.reason == "required_artifact_missing"]
    if blockers or errors:
        status = PortfolioCertificationStatus.insufficient_data if required_missing else PortfolioCertificationStatus.rejected
    elif any(check.status == "warning" for check in scorecard.checks):
        status = PortfolioCertificationStatus.conditional if policy.allow_conditional else PortfolioCertificationStatus.needs_review
    else:
        status = PortfolioCertificationStatus.certified
    passed = status in {PortfolioCertificationStatus.certified, PortfolioCertificationStatus.conditional}
    reasons = [check.name for check in failed] if failed else []
    return PortfolioCertificationDecision(
        portfolio_policy_id=scorecard.portfolio_policy_id,
        factor_id=scorecard.factor_id,
        status=status,
        passed=passed,
        reasons=reasons,
        required_remediation=[f"review_{check.name}" for check in failed],
        checks=scorecard.summary,
        policy_id=policy.policy_id,
        policy_profile=policy.profile_name,
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    )
