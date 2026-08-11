"""Feature promotion policy, evidence, review, decision, reporting, and command workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class FeaturePromotionStatus:
    report_only = "report_only"
    alpha_eligible = "alpha_eligible"
    risk_filter_only = "risk_filter_only"
    blocked = "blocked"
    needs_review = "needs_review"
    deprecated = "deprecated"


class FeaturePromotionSeverity:
    info = "info"
    warning = "warning"
    error = "error"
    blocker = "blocker"


@dataclass(frozen=True)
class FeaturePromotionPolicy:
    policy_id: str
    policy_name: str
    feature_set_name: str
    feature_set_hash: str
    default_weak_pit_action: str = FeaturePromotionStatus.needs_review
    default_unsafe_action: str = FeaturePromotionStatus.blocked
    require_availability_field: bool = True
    require_leakage_audit: bool = False
    require_coverage_min: float = 0.0
    require_manual_approval_for_weak_pit: bool = True
    allowed_feature_families: list[str] = field(default_factory=list)
    denied_features: list[str] = field(default_factory=list)
    family_rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeaturePromotionCandidate:
    feature_name: str
    feature_family: str
    feature_set_name: str
    feature_set_hash: str
    required_datasets: list[str]
    optional_datasets: list[str]
    source_fields: list[str]
    date_field: str
    availability_field: str | None
    pit_safety: str
    current_default_enabled: bool
    proposed_status: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeaturePromotionEvidence:
    feature_name: str
    pit_contract_status: str
    availability_field_status: str
    coverage_status: str
    leakage_audit_status: str
    sample_alignment_status: str
    feature_tensor_coverage: float
    weak_pit_reason: str
    artifact_refs: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeaturePromotionDecision:
    feature_name: str
    decision: str
    status: str
    approved_for_alpha: bool
    approved_for_risk_filter: bool
    blocked_reason: str | None = None
    reviewer: str | None = None
    approval_id: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeaturePromotionReviewPackage:
    review_id: str
    policy: dict[str, Any]
    summary: dict[str, Any]
    candidates: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any



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

from pathlib import Path
from typing import Any



def build_promotion_candidates(manifest: dict[str, Any], policy) -> list[FeaturePromotionCandidate]:
    feature_set_name = str(manifest.get("feature_set_name") or "")
    feature_set_hash = str(manifest.get("content_hash") or manifest.get("feature_set_hash") or "")
    candidates: list[FeaturePromotionCandidate] = []
    for item in manifest.get("feature_definitions", []):
        if not isinstance(item, dict):
            continue
        status, reason = feature_default_status(item, policy)
        candidates.append(
            FeaturePromotionCandidate(
                feature_name=str(item.get("feature_name") or ""),
                feature_family=str(item.get("family") or ""),
                feature_set_name=feature_set_name,
                feature_set_hash=feature_set_hash,
                required_datasets=[str(value) for value in item.get("required_datasets", [])],
                optional_datasets=[str(value) for value in item.get("optional_datasets", [])],
                source_fields=[str(value) for value in item.get("source_fields", [])],
                date_field=str(item.get("date_field") or "trade_date"),
                availability_field=item.get("availability_field"),
                pit_safety=str(item.get("pit_safety") or "pit_safe"),
                current_default_enabled=bool(item.get("default_enabled", True)),
                proposed_status=status,
                reason=reason,
                metadata={
                    "used_for_alpha": bool(item.get("used_for_alpha", True)),
                    "used_for_filter": bool(item.get("used_for_filter", False)),
                    "used_for_risk": bool(item.get("used_for_risk", False)),
                },
            )
        )
    return candidates


def build_feature_promotion_evidence(
    *,
    manifest: dict[str, Any],
    policy,
    feature_family_readiness_path: str | Path | None = None,
    feature_pit_alignment_report_path: str | Path | None = None,
    feature_build_warnings_path: str | Path | None = None,
    feature_coverage_report_path: str | Path | None = None,
    pit_validation_report_path: str | Path | None = None,
    leakage_audit_report_path: str | Path | None = None,
    raw_landing_report_path: str | Path | None = None,
    research_data_readiness_report_path: str | Path | None = None,
) -> tuple[list[FeaturePromotionEvidence], dict[str, Any]]:
    coverage = _coverage_by_feature(load_json(feature_coverage_report_path))
    pit_rows = _pit_by_feature(load_json(feature_pit_alignment_report_path))
    family_rows = _family_by_name(load_json(feature_family_readiness_path))
    warnings = load_jsonl(feature_build_warnings_path)
    pit_report = load_json(pit_validation_report_path)
    leakage_report = load_json(leakage_audit_report_path)
    raw_report = load_json(raw_landing_report_path)
    readiness_report = load_json(research_data_readiness_report_path)
    evidence: list[FeaturePromotionEvidence] = []
    for candidate in build_promotion_candidates(manifest, policy):
        cov = coverage.get(candidate.feature_name, {})
        pit = pit_rows.get(candidate.feature_name, {})
        tensor_coverage = float(cov.get("nonzero_ratio", cov.get("finite_ratio", 0.0)) or 0.0)
        availability_status = _availability_status(candidate)
        pit_status = str(pit.get("status") or ("weak_pit" if candidate.pit_safety != "pit_safe" else "safe"))
        coverage_status = "passed" if tensor_coverage >= float(policy.require_coverage_min) else "warning"
        leakage_status = _leakage_status(leakage_report, policy)
        sample_status = _sample_alignment_status(candidate, family_rows, warnings)
        if candidate.proposed_status == FeaturePromotionStatus.blocked:
            coverage_status = "blocked" if coverage_status == "warning" else coverage_status
        evidence.append(
            FeaturePromotionEvidence(
                feature_name=candidate.feature_name,
                pit_contract_status=pit_status,
                availability_field_status=availability_status,
                coverage_status=coverage_status,
                leakage_audit_status=leakage_status,
                sample_alignment_status=sample_status,
                feature_tensor_coverage=tensor_coverage,
                weak_pit_reason=candidate.reason if candidate.pit_safety != "pit_safe" else "",
                artifact_refs=_artifact_refs(
                    feature_family_readiness_path=feature_family_readiness_path,
                    feature_pit_alignment_report_path=feature_pit_alignment_report_path,
                    feature_build_warnings_path=feature_build_warnings_path,
                    feature_coverage_report_path=feature_coverage_report_path,
                    pit_validation_report_path=pit_validation_report_path,
                    leakage_audit_report_path=leakage_audit_report_path,
                    raw_landing_report_path=raw_landing_report_path,
                    research_data_readiness_report_path=research_data_readiness_report_path,
                ),
                metadata={
                    "feature_family": candidate.feature_family,
                    "proposed_status": candidate.proposed_status,
                    "required_datasets": candidate.required_datasets,
                    "optional_datasets": candidate.optional_datasets,
                    "raw_landing_status": raw_report.get("status") or "",
                    "research_readiness_status": (readiness_report.get("decision") or {}).get("status", readiness_report.get("status", "")),
                },
            )
        )
    return evidence, _evidence_summary(evidence, build_promotion_candidates(manifest, policy))


def _coverage_by_feature(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("feature_name")): dict(item)
        for item in payload.get("feature_summaries", [])
        if isinstance(item, dict) and item.get("feature_name")
    }


def _pit_by_feature(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("feature_name")): dict(item)
        for item in payload.get("features", [])
        if isinstance(item, dict) and item.get("feature_name")
    }


def _family_by_name(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("family")): dict(item)
        for item in payload.get("families", [])
        if isinstance(item, dict) and item.get("family")
    }


def _availability_status(candidate: FeaturePromotionCandidate) -> str:
    if candidate.availability_field:
        return "passed"
    if candidate.feature_family in REVIEW_REQUIRED_FAMILIES:
        return "missing_availability_field"
    return "not_required"


def _leakage_status(payload: dict[str, Any], policy) -> str:
    if not payload:
        return "missing" if policy.require_leakage_audit else "not_required"
    blockers = int(payload.get("blocker_count", 0) or 0)
    return "blocked" if blockers else "passed"


def _sample_alignment_status(candidate: FeaturePromotionCandidate, family_rows: dict[str, dict[str, Any]], warnings: list[dict[str, Any]]) -> str:
    family = family_rows.get(candidate.feature_family, {})
    if family.get("readiness") == "insufficient_data":
        return "insufficient_data"
    if any(row.get("feature_name") == candidate.feature_name for row in warnings):
        return "warning"
    return "passed" if family else "needs_review"


def _artifact_refs(**paths: str | Path | None) -> dict[str, str]:
    return {key: str(value) for key, value in paths.items() if value and Path(value).exists()}


def _evidence_summary(evidence: list[FeaturePromotionEvidence], candidates: list[FeaturePromotionCandidate]) -> dict[str, Any]:
    return {
        "candidate_count": len(candidates),
        "evidence_count": len(evidence),
        "alpha_eligible_default_count": sum(item.proposed_status == FeaturePromotionStatus.alpha_eligible for item in candidates),
        "needs_review_count": sum(item.proposed_status == FeaturePromotionStatus.needs_review for item in candidates),
        "report_only_count": sum(item.proposed_status == FeaturePromotionStatus.report_only for item in candidates),
        "risk_filter_only_count": sum(item.proposed_status == FeaturePromotionStatus.risk_filter_only for item in candidates),
        "blocked_count": sum(item.proposed_status == FeaturePromotionStatus.blocked for item in candidates),
        "weak_pit_feature_count": sum(item.pit_safety != "pit_safe" for item in candidates),
        "missing_availability_count": sum(item.availability_field_status == "missing_availability_field" for item in evidence),
        "leakage_blocked_count": sum(item.leakage_audit_status == "blocked" for item in evidence),
    }

import hashlib
import json
from datetime import datetime
from typing import Any



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
        created_at=_promotion_review_utc_now(),
        metadata=metadata or {},
    )


def _promotion_review_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.platform.governance.approval.models import ApprovalStatus
from auto_alpha.platform.governance.approval.store import LocalApprovalStore



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
    now = _promotion_decision_utc_now()
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


def _promotion_decision_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import json
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact


def write_policy_artifacts(policy: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = write_json_artifact(target / "feature_promotion_policy.json", policy, "feature_promotion_policy", "feature_promotion")
    md_path = target / "feature_promotion_policy.md"
    md_path.write_text(_policy_markdown(policy), encoding="utf-8")
    return {"feature_promotion_policy_path": str(json_path), "feature_promotion_policy_md_path": str(md_path)}


def write_evidence_artifacts(evidence: list[dict[str, Any]], summary: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    evidence_path = write_jsonl_artifact(target / "feature_promotion_evidence.jsonl", evidence, "feature_promotion_evidence", "feature_promotion")
    report = {"status": "success", "summary": summary, "evidence_count": len(evidence), "top_evidence": evidence[:20]}
    report_path = write_json_artifact(target / "feature_promotion_evidence_report.json", report, "feature_promotion_evidence_report", "feature_promotion")
    md_path = target / "feature_promotion_evidence_report.md"
    md_path.write_text(_evidence_markdown(report), encoding="utf-8")
    return {
        "feature_promotion_evidence_path": str(evidence_path),
        "feature_promotion_evidence_report_path": str(report_path),
        "feature_promotion_evidence_report_md_path": str(md_path),
    }


def write_review_package(package: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = write_json_artifact(target / "feature_promotion_review_package.json", package, "feature_promotion_review_package", "feature_promotion")
    md_path = target / "feature_promotion_review_package.md"
    md_path.write_text(_review_markdown(package), encoding="utf-8")
    return {
        "feature_promotion_review_package_path": str(path),
        "feature_promotion_review_package_md_path": str(md_path),
    }


def write_application_artifacts(
    *,
    decisions: list[dict[str, Any]],
    allowlist: dict[str, Any],
    denylist: dict[str, Any],
    report: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    decisions_path = write_jsonl_artifact(target / "feature_promotion_decisions.jsonl", decisions, "feature_promotion_decisions", "feature_promotion")
    allowlist_path = write_json_artifact(target / "feature_promotion_allowlist.json", allowlist, "feature_promotion_allowlist", "feature_promotion")
    denylist_path = write_json_artifact(target / "feature_promotion_denylist.json", denylist, "feature_promotion_denylist", "feature_promotion")
    report_path = write_json_artifact(target / "feature_promotion_application_report.json", report, "feature_promotion_application_report", "feature_promotion")
    md_path = target / "feature_promotion_application_report.md"
    md_path.write_text(_application_markdown(report), encoding="utf-8")
    return {
        "feature_promotion_decisions_path": str(decisions_path),
        "feature_promotion_allowlist_path": str(allowlist_path),
        "feature_promotion_denylist_path": str(denylist_path),
        "feature_promotion_application_report_path": str(report_path),
        "feature_promotion_application_report_md_path": str(md_path),
    }


def _policy_markdown(policy: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Feature Promotion Policy",
            "",
            f"- Policy: `{policy.get('policy_name')}`",
            f"- Policy id: `{policy.get('policy_id')}`",
            f"- Feature set: `{policy.get('feature_set_name')}`",
            f"- Feature set hash: `{policy.get('feature_set_hash')}`",
            f"- Weak PIT default: `{policy.get('default_weak_pit_action')}`",
            f"- Unsafe default: `{policy.get('default_unsafe_action')}`",
        ]
    )


def _evidence_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = ["# Feature Promotion Evidence", ""]
    for key in sorted(summary):
        lines.append(f"- {key}: `{summary[key]}`")
    return "\n".join(lines)


def _review_markdown(package: dict[str, Any]) -> str:
    summary = package.get("summary", {})
    lines = ["# Feature Promotion Review Package", "", f"- Review id: `{package.get('review_id')}`"]
    for key in sorted(summary):
        lines.append(f"- {key}: `{summary[key]}`")
    return "\n".join(lines)


def _application_markdown(report: dict[str, Any]) -> str:
    lines = ["# Feature Promotion Application", ""]
    for key in sorted(report):
        value = report[key]
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"- {key}: `{value}`")
    return "\n".join(lines)

import argparse
import json
from pathlib import Path

from auto_alpha.platform.governance.approval.models import ApprovalBatch, ApprovalType
from auto_alpha.platform.governance.approval.store import LocalApprovalStore
from auto_alpha.research.features.catalog import FEATURE_SET_V3
from auto_alpha.research.features.catalog import build_feature_set_manifest



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review and promote PIT-sensitive feature families.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["init-policy", "validate-policy", "build-evidence", "create-review", "apply-approved", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_common(cmd)
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--feature-set-manifest-path")
    parser.add_argument("--feature-family-readiness-path")
    parser.add_argument("--feature-pit-alignment-report-path")
    parser.add_argument("--feature-build-warnings-path")
    parser.add_argument("--feature-coverage-report-path")
    parser.add_argument("--pit-validation-report-path")
    parser.add_argument("--leakage-audit-report-path")
    parser.add_argument("--raw-landing-report-path")
    parser.add_argument("--research-data-readiness-report-path")
    parser.add_argument("--feature-promotion-policy-path")
    parser.add_argument("--feature-promotion-evidence-path")
    parser.add_argument("--feature-promotion-review-package-path")
    parser.add_argument("--feature-promotion-decisions-path")
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--approval-id")
    parser.add_argument("--reviewer", default="local_feature_reviewer")
    parser.add_argument("--comment")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--data-dir")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0


def _run(args: argparse.Namespace) -> dict:
    if args.command == "smoke":
        return _run_smoke(args)
    if args.command == "validate-policy":
        policy = _load_policy_payload(args)
        return {"status": "success", "policy_hash": policy_hash(policy), "policy": policy}
    if args.command == "init-policy":
        manifest = _load_manifest(args)
        policy = create_default_policy(manifest).to_dict()
        policy["policy_hash"] = policy_hash(policy)
        paths = write_policy_artifacts(policy, args.output_dir)
        return {"status": "success", "policy_hash": policy["policy_hash"], "paths": paths, "policy": policy}
    if args.command == "build-evidence":
        manifest = _load_manifest(args)
        policy = load_policy(args.feature_promotion_policy_path, manifest) or create_default_policy(manifest)
        evidence, summary = build_feature_promotion_evidence(
            manifest=manifest,
            policy=policy,
            feature_family_readiness_path=args.feature_family_readiness_path,
            feature_pit_alignment_report_path=args.feature_pit_alignment_report_path,
            feature_build_warnings_path=args.feature_build_warnings_path,
            feature_coverage_report_path=args.feature_coverage_report_path,
            pit_validation_report_path=args.pit_validation_report_path,
            leakage_audit_report_path=args.leakage_audit_report_path,
            raw_landing_report_path=args.raw_landing_report_path,
            research_data_readiness_report_path=args.research_data_readiness_report_path,
        )
        paths = write_evidence_artifacts([item.to_dict() for item in evidence], summary, args.output_dir)
        return {"status": "success", "summary": summary, "paths": paths}
    if args.command == "create-review":
        return _create_review(args)
    if args.command == "apply-approved":
        return _apply_approved(args)
    if args.command == "report":
        package = load_json(args.feature_promotion_review_package_path)
        return {"status": "success" if package else "missing", "review_package": package}
    raise ValueError(f"unsupported command: {args.command}")


def _create_review(args: argparse.Namespace) -> dict:
    manifest = _load_manifest(args)
    policy = load_policy(args.feature_promotion_policy_path, manifest) or create_default_policy(manifest)
    evidence, summary = build_feature_promotion_evidence(
        manifest=manifest,
        policy=policy,
        feature_family_readiness_path=args.feature_family_readiness_path,
        feature_pit_alignment_report_path=args.feature_pit_alignment_report_path,
        feature_build_warnings_path=args.feature_build_warnings_path,
        feature_coverage_report_path=args.feature_coverage_report_path,
        pit_validation_report_path=args.pit_validation_report_path,
        leakage_audit_report_path=args.leakage_audit_report_path,
        raw_landing_report_path=args.raw_landing_report_path,
        research_data_readiness_report_path=args.research_data_readiness_report_path,
    )
    candidates = build_promotion_candidates(manifest, policy)
    package = make_review_package(policy, candidates, evidence, metadata={"evidence_summary": summary})
    paths = {}
    paths.update(write_evidence_artifacts([item.to_dict() for item in evidence], summary, args.output_dir))
    paths.update(write_review_package(package.to_dict(), args.output_dir))
    approval_id = None
    if args.approval_store_dir:
        approval_id = package.review_id
        store = LocalApprovalStore(args.approval_store_dir)
        batch = ApprovalBatch(
            approval_id=approval_id,
            created_at=package.created_at,
            factor_id="feature_promotion",
            factor_type="feature_set",
            rebalance_date="",
            portfolio_method="feature_promotion",
            orders=[],
            approval_type=ApprovalType.feature_promotion_review,
            status="pending",
            metadata={
                "feature_promotion_policy_path": args.feature_promotion_policy_path,
                "feature_promotion_review_package_path": paths["feature_promotion_review_package_path"],
                "feature_promotion_summary": package.summary,
                "approved_feature_count": 0,
                "blocked_feature_count": package.summary.get("blocked_feature_count", 0),
                "weak_pit_feature_count": package.summary.get("weak_pit_feature_count", 0),
            },
        )
        store.save_batch(batch)
        paths["feature_promotion_approval_path"] = str(Path(args.approval_store_dir) / "approvals" / f"{approval_id}.json")
    return {"status": "success", "review_id": package.review_id, "approval_id": approval_id, "summary": package.summary, "paths": paths}


def _apply_approved(args: argparse.Namespace) -> dict:
    policy_payload = _load_policy_payload(args)
    review_package = load_json(args.feature_promotion_review_package_path)
    if args.approval_store_dir and args.approval_id:
        decisions, context = decisions_from_approval(
            approval_store_dir=args.approval_store_dir,
            approval_id=args.approval_id,
            review_package_path=args.feature_promotion_review_package_path,
        )
        review_package = context.get("review_package") or review_package
    elif args.feature_promotion_decisions_path:
        decisions = load_decisions(args.feature_promotion_decisions_path)
    else:
        if not review_package:
            raise ValueError("review package or decisions are required")
        decisions = default_decisions_from_review_package(review_package, reviewer=args.reviewer)
    decisions_path = Path(args.output_dir) / "feature_promotion_decisions.jsonl"
    write_decisions(decisions_path, decisions)
    allowlist, denylist, report = build_allow_deny_lists(policy=policy_payload, decisions=decisions, review_package=review_package)
    paths = write_application_artifacts(
        decisions=[item.to_dict() for item in decisions],
        allowlist=allowlist,
        denylist=denylist,
        report=report,
        output_dir=args.output_dir,
    )
    return {"status": "success", "summary": report, "paths": paths}


def _run_smoke(args: argparse.Namespace) -> dict:
    from auto_alpha.research.features.factory import main as run_features_main

    output_dir = Path(args.output_dir)
    feature_dir = output_dir / "features_v3"
    data_dir = Path(args.data_dir) if args.data_dir else output_dir / "sample_data"
    if not (feature_dir / "feature_set_manifest.json").exists():
        from auto_alpha.data.ingestion.pipeline.run_pipeline import main as run_pipeline_main

        run_pipeline_main(
            [
                "--sync",
                "--provider",
                "sample",
                "--data-dir",
                str(data_dir),
                "--validate",
                "--mode",
                "overwrite",
                "--index-codes",
                "000300.SH",
            ]
        )
        run_features_main(
            [
                "build",
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(feature_dir),
                "--feature-set-name",
                FEATURE_SET_V3,
            ]
        )
    args.feature_set_manifest_path = str(feature_dir / "feature_set_manifest.json")
    args.feature_family_readiness_path = str(feature_dir / "feature_family_readiness.json")
    args.feature_pit_alignment_report_path = str(feature_dir / "feature_pit_alignment_report.json")
    args.feature_build_warnings_path = str(feature_dir / "feature_build_warnings.jsonl")
    args.feature_coverage_report_path = str(feature_dir / "feature_coverage_report.json")
    init_args = vars(args).copy()
    init_args["command"] = "init-policy"
    policy_payload = _run(argparse.Namespace(**init_args))
    args.feature_promotion_policy_path = policy_payload["paths"]["feature_promotion_policy_path"]
    review_payload = _create_review(args)
    args.feature_promotion_review_package_path = review_payload["paths"]["feature_promotion_review_package_path"]
    apply_payload = _apply_approved(args)
    return {
        "status": "success",
        "policy_hash": policy_payload["policy_hash"],
        "review_id": review_payload["review_id"],
        "evidence_count": review_payload["summary"]["evidence_count"],
        "allowlist_count": apply_payload["summary"]["allowlist_count"],
        "denylist_count": apply_payload["summary"]["denylist_count"],
        "paths": policy_payload["paths"] | review_payload["paths"] | apply_payload["paths"],
    }


def _load_manifest(args: argparse.Namespace) -> dict:
    if args.feature_set_manifest_path:
        payload = load_json(args.feature_set_manifest_path)
        if payload:
            return payload
    return build_feature_set_manifest(FEATURE_SET_V3).to_dict()


def _load_policy_payload(args: argparse.Namespace) -> dict:
    payload = load_json(args.feature_promotion_policy_path)
    if payload:
        return payload
    manifest = _load_manifest(args)
    policy = create_default_policy(manifest).to_dict()
    policy["policy_hash"] = policy_hash(policy)
    return policy


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "FeaturePromotionCandidate",
    "FeaturePromotionDecision",
    "FeaturePromotionEvidence",
    "FeaturePromotionGate",
    "FeaturePromotionPolicy",
    "FeaturePromotionReviewPackage",
    "FeaturePromotionSeverity",
    "FeaturePromotionStatus",
    "apply_promotion_to_manifest",
    "load_promotion_gate",
    "policy_hash",
]
