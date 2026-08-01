"""Apply feature promotion decisions into allowlist and denylist artifacts."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from approval.models import ApprovalStatus
from approval.store import LocalApprovalStore

from .models import FeaturePromotionDecision, FeaturePromotionStatus
from .policy import load_json, load_jsonl, policy_hash


def decision_from_payload(payload: dict[str, Any]) -> FeaturePromotionDecision:
    return FeaturePromotionDecision(
        feature_name=str(payload.get("feature_name") or ""),
        decision=str(payload.get("decision") or payload.get("status") or FeaturePromotionStatus.needs_review),
        status=str(payload.get("status") or payload.get("decision") or FeaturePromotionStatus.needs_review),
        approved_for_alpha=bool(payload.get("approved_for_alpha", False)),
        approved_for_risk_filter=bool(payload.get("approved_for_risk_filter", False)),
        blocked_reason=payload.get("blocked_reason"),
        reviewer=payload.get("reviewer"),
        approval_id=payload.get("approval_id"),
        expires_at=payload.get("expires_at"),
        metadata=dict(payload.get("metadata") or {}),
    )


def default_decisions_from_review_package(
    review_package: dict[str, Any],
    *,
    reviewer: str | None = None,
    approval_id: str | None = None,
    approve_alpha_for_pit_safe: bool = True,
) -> list[FeaturePromotionDecision]:
    decisions: list[FeaturePromotionDecision] = []
    for candidate in review_package.get("candidates", []):
        status = str(candidate.get("proposed_status") or FeaturePromotionStatus.needs_review)
        approved_alpha = approve_alpha_for_pit_safe and status == FeaturePromotionStatus.alpha_eligible
        approved_risk = status == FeaturePromotionStatus.risk_filter_only
        decisions.append(
            FeaturePromotionDecision(
                feature_name=str(candidate.get("feature_name") or ""),
                decision=status,
                status=status,
                approved_for_alpha=approved_alpha,
                approved_for_risk_filter=approved_risk,
                blocked_reason=str(candidate.get("reason") or "") if status == FeaturePromotionStatus.blocked else None,
                reviewer=reviewer,
                approval_id=approval_id,
                metadata={"source": "review_package_default", "feature_family": candidate.get("feature_family")},
            )
        )
    return decisions


def decisions_from_approval(
    *,
    approval_store_dir: str | Path,
    approval_id: str,
    review_package_path: str | Path | None = None,
) -> tuple[list[FeaturePromotionDecision], dict[str, Any]]:
    store = LocalApprovalStore(approval_store_dir)
    batch = store.load_batch(approval_id)
    if batch.status != ApprovalStatus.approved:
        raise ValueError(f"feature promotion approval is not approved: {approval_id} is {batch.status}")
    package_path = review_package_path or batch.metadata.get("feature_promotion_review_package_path")
    if not package_path:
        raise ValueError("review package path is required")
    package = load_json(package_path)
    reviewer = batch.decision.reviewer if batch.decision else None
    explicit = batch.metadata.get("feature_promotion_decisions")
    if isinstance(explicit, list) and explicit:
        decisions = [decision_from_payload(item | {"approval_id": approval_id, "reviewer": reviewer}) for item in explicit]
    else:
        decisions = default_decisions_from_review_package(package, reviewer=reviewer, approval_id=approval_id)
    return decisions, {"approval": batch.to_dict(), "review_package": package}


def build_allow_deny_lists(
    *,
    policy: dict[str, Any],
    decisions: list[FeaturePromotionDecision],
    review_package: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    now = _utc_now()
    alpha = sorted({item.feature_name for item in decisions if item.approved_for_alpha and item.status == FeaturePromotionStatus.alpha_eligible})
    risk = sorted({item.feature_name for item in decisions if item.approved_for_risk_filter or item.status == FeaturePromotionStatus.risk_filter_only})
    blocked = sorted({item.feature_name for item in decisions if item.status == FeaturePromotionStatus.blocked})
    expired = sorted({item.feature_name for item in decisions if item.expires_at and str(item.expires_at) < now})
    feature_set_hash = str(policy.get("feature_set_hash") or (review_package or {}).get("policy", {}).get("feature_set_hash") or "")
    p_hash = policy.get("policy_hash") or policy_hash(policy)
    allowlist = {
        "policy_hash": p_hash,
        "feature_set_name": policy.get("feature_set_name"),
        "feature_set_hash": feature_set_hash,
        "created_at": now,
        "alpha_eligible_features": alpha,
        "risk_filter_only_features": risk,
        "promoted_weak_pit_features": [
            item.feature_name
            for item in decisions
            if item.approved_for_alpha and (item.metadata or {}).get("pit_safety") not in {None, "pit_safe"}
        ],
        "expired_features": expired,
        "decisions": [item.to_dict() for item in decisions],
    }
    denylist = {
        "policy_hash": p_hash,
        "feature_set_name": policy.get("feature_set_name"),
        "feature_set_hash": feature_set_hash,
        "created_at": now,
        "blocked_features": blocked,
        "denied_features": blocked,
        "decisions": [item.to_dict() for item in decisions if item.status == FeaturePromotionStatus.blocked],
    }
    report = {
        "status": "success",
        "policy_hash": p_hash,
        "feature_set_hash": feature_set_hash,
        "decision_count": len(decisions),
        "allowlist_count": len(alpha),
        "risk_filter_count": len(risk),
        "denylist_count": len(blocked),
        "expired_promotion_count": len(expired),
        "created_at": now,
    }
    return allowlist, denylist, report


def load_decisions(path: str | Path | None) -> list[FeaturePromotionDecision]:
    return [decision_from_payload(item) for item in load_jsonl(path)]


def write_decisions(path: str | Path, decisions: list[FeaturePromotionDecision]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for item in decisions:
            handle.write(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return target


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
