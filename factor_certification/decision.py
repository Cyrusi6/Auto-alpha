"""Certification decision logic."""

from __future__ import annotations

from datetime import datetime

from .models import CertificationPolicy, CertificationStatus, FactorCertificationDecision, FactorCertificationScorecard


def make_certification_decision(
    scorecard: FactorCertificationScorecard,
    policy: CertificationPolicy,
) -> FactorCertificationDecision:
    failed = [check for check in scorecard.checks if check.status == "failed"]
    blockers = [check for check in failed if check.severity == "blocker"]
    errors = [check for check in failed if check.severity == "error"]
    required_missing = [check for check in failed if check.reason == "required_artifact_missing"]
    reasons = [check.name for check in failed]
    if blockers:
        status = CertificationStatus.rejected
    elif required_missing:
        status = CertificationStatus.insufficient_data
    elif errors:
        status = CertificationStatus.conditional
    elif any(check.status == "warning" for check in scorecard.checks):
        status = CertificationStatus.conditional
    else:
        status = CertificationStatus.certified
    passed = status in {CertificationStatus.certified, CertificationStatus.conditional}
    remediation = [f"review_{name}" for name in reasons] if status == CertificationStatus.conditional else list(reasons)
    return FactorCertificationDecision(
        factor_id=scorecard.factor_id,
        status=status,
        passed=passed,
        reasons=reasons,
        required_remediation=remediation,
        checks=scorecard.summary,
        policy_id=policy.policy_id,
        policy_profile=policy.profile_name,
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    )
