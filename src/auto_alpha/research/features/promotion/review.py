"""Review package creation for feature promotion."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from .models import FeaturePromotionReviewPackage


def make_review_package(policy, candidates: list, evidence: list, metadata: dict[str, Any] | None = None) -> FeaturePromotionReviewPackage:
    candidate_payloads = [item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in candidates]
    evidence_payloads = [item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in evidence]
    summary = {
        "candidate_count": len(candidate_payloads),
        "evidence_count": len(evidence_payloads),
        "weak_pit_feature_count": sum(item.get("pit_safety") != "pit_safe" for item in candidate_payloads),
        "blocked_feature_count": sum(item.get("proposed_status") == "blocked" for item in candidate_payloads),
        "needs_review_count": sum(item.get("proposed_status") == "needs_review" for item in candidate_payloads),
        "alpha_eligible_default_count": sum(item.get("proposed_status") == "alpha_eligible" for item in candidate_payloads),
    }
    policy_payload = policy.to_dict() if hasattr(policy, "to_dict") else dict(policy)
    review_id = "feature_promotion_review_" + hashlib.sha256(
        json.dumps(
            {
                "policy_id": policy_payload.get("policy_id"),
                "feature_set_hash": policy_payload.get("feature_set_hash"),
                "candidate_count": len(candidate_payloads),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return FeaturePromotionReviewPackage(
        review_id=review_id,
        policy=policy_payload,
        summary=summary,
        candidates=candidate_payloads,
        evidence=evidence_payloads,
        created_at=_utc_now(),
        metadata=metadata or {},
    )


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
