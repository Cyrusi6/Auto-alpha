"""Feature promotion policies and formula gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import FeaturePromotionPolicy, FeaturePromotionStatus


REVIEW_REQUIRED_FAMILIES = {
    "financial_statement",
    "earnings_event",
    "holder_structure",
    "pledge_repurchase_unlock",
    "abnormal_trading",
    "northbound",
}
RISK_FILTER_FAMILIES = {"suspension_status", "limit_suspension", "risk"}


def stable_payload_hash(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def load_jsonl(path: str | Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    target = Path(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def policy_hash(policy: FeaturePromotionPolicy | dict[str, Any]) -> str:
    payload = policy.to_dict() if hasattr(policy, "to_dict") else dict(policy)
    payload = {key: value for key, value in payload.items() if key not in {"policy_hash", "artifact_metadata"}}
    return stable_payload_hash(payload)


def create_default_policy(manifest: dict[str, Any], policy_name: str = "default_feature_promotion_policy") -> FeaturePromotionPolicy:
    feature_set_name = str(manifest.get("feature_set_name") or "unknown")
    feature_set_hash = str(manifest.get("content_hash") or manifest.get("feature_set_hash") or "")
    base = {
        "policy_name": policy_name,
        "feature_set_name": feature_set_name,
        "feature_set_hash": feature_set_hash,
    }
    policy_id = "feature_promotion_" + stable_payload_hash(base)[:16]
    return FeaturePromotionPolicy(
        policy_id=policy_id,
        policy_name=policy_name,
        feature_set_name=feature_set_name,
        feature_set_hash=feature_set_hash,
        default_weak_pit_action=FeaturePromotionStatus.needs_review,
        default_unsafe_action=FeaturePromotionStatus.blocked,
        require_availability_field=True,
        require_leakage_audit=False,
        require_coverage_min=0.0,
        require_manual_approval_for_weak_pit=True,
        family_rules={
            family: {"default_status": FeaturePromotionStatus.needs_review}
            for family in sorted(REVIEW_REQUIRED_FAMILIES)
        }
        | {
            family: {"default_status": FeaturePromotionStatus.risk_filter_only}
            for family in sorted(RISK_FILTER_FAMILIES)
        },
        metadata={"policy_hash": stable_payload_hash(base)},
    )


def policy_from_payload(payload: dict[str, Any]) -> FeaturePromotionPolicy:
    return FeaturePromotionPolicy(
        policy_id=str(payload.get("policy_id") or "feature_promotion_policy"),
        policy_name=str(payload.get("policy_name") or "feature_promotion_policy"),
        feature_set_name=str(payload.get("feature_set_name") or ""),
        feature_set_hash=str(payload.get("feature_set_hash") or ""),
        default_weak_pit_action=str(payload.get("default_weak_pit_action") or FeaturePromotionStatus.needs_review),
        default_unsafe_action=str(payload.get("default_unsafe_action") or FeaturePromotionStatus.blocked),
        require_availability_field=bool(payload.get("require_availability_field", True)),
        require_leakage_audit=bool(payload.get("require_leakage_audit", False)),
        require_coverage_min=float(payload.get("require_coverage_min", 0.0) or 0.0),
        require_manual_approval_for_weak_pit=bool(payload.get("require_manual_approval_for_weak_pit", True)),
        allowed_feature_families=[str(item) for item in payload.get("allowed_feature_families", [])],
        denied_features=[str(item) for item in payload.get("denied_features", [])],
        family_rules={str(key): dict(value) for key, value in dict(payload.get("family_rules", {})).items()},
        metadata=dict(payload.get("metadata") or {}),
    )


def load_policy(path: str | Path | None, manifest: dict[str, Any] | None = None) -> FeaturePromotionPolicy | None:
    payload = load_json(path)
    if payload:
        return policy_from_payload(payload)
    if manifest:
        return create_default_policy(manifest)
    return None


def feature_default_status(feature: dict[str, Any], policy: FeaturePromotionPolicy) -> tuple[str, str]:
    name = str(feature.get("feature_name") or "")
    family = str(feature.get("family") or "")
    if name in set(policy.denied_features):
        return FeaturePromotionStatus.blocked, "denied_by_policy"
    if policy.allowed_feature_families and family not in set(policy.allowed_feature_families):
        return FeaturePromotionStatus.blocked, "family_not_allowed"
    if not bool(feature.get("default_enabled", True)):
        return FeaturePromotionStatus.blocked, "feature_default_disabled"
    if bool(feature.get("used_for_filter", False)) or bool(feature.get("used_for_risk", False)) or family in RISK_FILTER_FAMILIES:
        return FeaturePromotionStatus.risk_filter_only, "risk_or_filter_feature"
    if str(feature.get("pit_safety") or "pit_safe") != "pit_safe":
        return policy.default_weak_pit_action, "weak_pit_requires_review"
    if policy.require_availability_field and family in REVIEW_REQUIRED_FAMILIES and not feature.get("availability_field"):
        return policy.default_unsafe_action, "missing_required_availability_field"
    family_rule = policy.family_rules.get(family, {})
    if family_rule.get("default_status"):
        return str(family_rule["default_status"]), "family_rule"
    if bool(feature.get("used_for_alpha", True)):
        return FeaturePromotionStatus.alpha_eligible, "pit_safe_alpha_feature"
    return FeaturePromotionStatus.report_only, "not_alpha_feature"


@dataclass(frozen=True)
class FeaturePromotionGate:
    policy_hash: str | None
    require_promotion: bool
    alpha_eligible_features: set[str]
    risk_filter_features: set[str]
    blocked_features: set[str]
    allow_risk_filter_features: bool = False
    expired_features: set[str] | None = None

    def check_formula_names(self, names: list[str], feature_meta: dict[str, dict[str, Any]]) -> tuple[list[str], list[str], dict[str, Any]]:
        errors: list[str] = []
        warnings: list[str] = []
        used_features = [name for name in names if name in feature_meta]
        unapproved: list[str] = []
        risk_filter_used: list[str] = []
        blocked_used: list[str] = []
        for name in used_features:
            if name in self.blocked_features or name in (self.expired_features or set()):
                blocked_used.append(name)
                continue
            if self.require_promotion and name not in self.alpha_eligible_features:
                if name in self.risk_filter_features:
                    risk_filter_used.append(name)
                    if not self.allow_risk_filter_features:
                        continue
                else:
                    unapproved.append(name)
        if blocked_used:
            errors.append("blocked_feature_used:" + ",".join(sorted(blocked_used)))
        if unapproved:
            errors.append("unapproved_feature_used:" + ",".join(sorted(unapproved)))
        if risk_filter_used:
            message = "risk_filter_feature_used_as_alpha:" + ",".join(sorted(risk_filter_used))
            if self.allow_risk_filter_features:
                warnings.append(message)
            else:
                errors.append(message)
        return errors, warnings, {
            "feature_promotion_policy_hash": self.policy_hash,
            "used_features": used_features,
            "unapproved_feature_used": bool(unapproved),
            "weak_pit_promoted_feature_used": any(
                feature_meta.get(name, {}).get("pit_safety") != "pit_safe"
                for name in used_features
                if name in self.alpha_eligible_features
            ),
            "risk_filter_feature_used_as_alpha": bool(risk_filter_used),
            "blocked_feature_used": bool(blocked_used),
        }


def load_promotion_gate(
    *,
    policy_path: str | Path | None = None,
    allowlist_path: str | Path | None = None,
    denylist_path: str | Path | None = None,
    require_promotion: bool = False,
    allow_risk_filter_features: bool = False,
) -> FeaturePromotionGate | None:
    if not any([policy_path, allowlist_path, denylist_path, require_promotion]):
        return None
    policy_payload = load_json(policy_path)
    allowlist = load_json(allowlist_path)
    denylist = load_json(denylist_path)
    alpha = set(str(item) for item in allowlist.get("alpha_eligible_features", []))
    risk = set(str(item) for item in allowlist.get("risk_filter_only_features", []))
    blocked = set(str(item) for item in denylist.get("blocked_features", []))
    blocked.update(str(item) for item in policy_payload.get("denied_features", []))
    expired = set(str(item) for item in allowlist.get("expired_features", []))
    return FeaturePromotionGate(
        policy_hash=str(allowlist.get("policy_hash") or policy_payload.get("policy_hash") or (policy_hash(policy_payload) if policy_payload else "")),
        require_promotion=bool(require_promotion),
        alpha_eligible_features=alpha,
        risk_filter_features=risk,
        blocked_features=blocked,
        allow_risk_filter_features=bool(allow_risk_filter_features),
        expired_features=expired,
    )


def apply_promotion_to_manifest(
    manifest: dict[str, Any],
    *,
    policy_path: str | Path | None = None,
    allowlist_path: str | Path | None = None,
    denylist_path: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = load_policy(policy_path, manifest) or create_default_policy(manifest)
    allowlist = load_json(allowlist_path)
    denylist = load_json(denylist_path)
    alpha = set(str(item) for item in allowlist.get("alpha_eligible_features", []))
    risk = set(str(item) for item in allowlist.get("risk_filter_only_features", []))
    blocked = set(str(item) for item in denylist.get("blocked_features", []))
    promoted_weak = []
    updated = dict(manifest)
    definitions = []
    for item in manifest.get("feature_definitions", []):
        feature = dict(item)
        name = str(feature.get("feature_name") or "")
        default_status, reason = feature_default_status(feature, policy)
        status = FeaturePromotionStatus.alpha_eligible if name in alpha else default_status
        if name in risk:
            status = FeaturePromotionStatus.risk_filter_only
        if name in blocked:
            status = FeaturePromotionStatus.blocked
        feature["promotion_status"] = status
        feature["alpha_eligible"] = status == FeaturePromotionStatus.alpha_eligible
        feature["risk_filter_only"] = status == FeaturePromotionStatus.risk_filter_only
        feature["blocked"] = status == FeaturePromotionStatus.blocked
        feature["promotion_reason"] = reason
        if feature["alpha_eligible"] and feature.get("pit_safety") != "pit_safe":
            promoted_weak.append(name)
        definitions.append(feature)
    updated["feature_definitions"] = definitions
    updated["feature_promotion_policy_hash"] = policy_hash(policy)
    updated["feature_promotion_summary"] = _manifest_promotion_summary(definitions, promoted_weak)
    updated["content_hash"] = stable_payload_hash(
        {key: value for key, value in updated.items() if key not in {"content_hash", "artifact_metadata"}}
    )
    return updated, updated["feature_promotion_summary"]


def _manifest_promotion_summary(definitions: list[dict[str, Any]], promoted_weak: list[str]) -> dict[str, Any]:
    return {
        "alpha_eligible_feature_count": sum(bool(item.get("alpha_eligible")) for item in definitions),
        "risk_filter_feature_count": sum(bool(item.get("risk_filter_only")) for item in definitions),
        "blocked_feature_count": sum(bool(item.get("blocked")) for item in definitions),
        "weak_pit_promoted_count": len(promoted_weak),
        "promoted_weak_pit_features": sorted(promoted_weak),
    }
