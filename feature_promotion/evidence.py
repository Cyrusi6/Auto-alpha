"""Build feature promotion evidence from local feature artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import FeaturePromotionCandidate, FeaturePromotionEvidence, FeaturePromotionStatus
from .policy import REVIEW_REQUIRED_FAMILIES, feature_default_status, load_json, load_jsonl


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
