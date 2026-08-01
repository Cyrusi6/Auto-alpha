"""Build factor certification scorecards from validation artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import CertificationPolicy, FactorCertificationCheck, FactorCertificationScorecard


def build_factor_certification_scorecard(
    factor_id: str,
    policy: CertificationPolicy,
    artifact_paths: dict[str, str | None],
) -> FactorCertificationScorecard:
    checks = [
        _data_freeze_check(policy, artifact_paths),
        _report_required_check("validation_lab_check", policy.require_validation_lab, artifact_paths.get("validation_lab_report_path")),
        _metric_check(
            "out_of_sample_check",
            _payload(artifact_paths.get("factor_validation_summary_path")).get("out_of_sample_score"),
            policy.min_out_of_sample_score,
            "gte",
            artifact_paths.get("factor_validation_summary_path"),
        ),
        _metric_check(
            "window_pass_ratio_check",
            _payload(artifact_paths.get("factor_validation_summary_path")).get("window_pass_ratio"),
            policy.min_window_pass_ratio,
            "gte",
            artifact_paths.get("factor_validation_summary_path"),
        ),
        _report_required_check("multiple_testing_check", policy.require_multiple_testing, artifact_paths.get("multiple_testing_report_path")),
        _metric_check(
            "overfit_risk_check",
            _payload(artifact_paths.get("overfit_risk_report_path")).get("pbo_estimate"),
            policy.max_pbo,
            "lte",
            artifact_paths.get("overfit_risk_report_path"),
            required=policy.require_overfit_risk,
        ),
        _metric_check(
            "deflated_ic_check",
            _payload(artifact_paths.get("overfit_risk_report_path")).get("deflated_ic_like_score"),
            policy.min_deflated_ic_score,
            "gte",
            artifact_paths.get("overfit_risk_report_path"),
            required=policy.require_overfit_risk,
        ),
        _metric_check(
            "placebo_check",
            _payload(artifact_paths.get("placebo_test_report_path")).get("candidate_vs_placebo_percentile"),
            policy.min_placebo_percentile,
            "gte",
            artifact_paths.get("placebo_test_report_path"),
            required=policy.require_placebo,
        ),
        _metric_check(
            "null_exceedance_check",
            _payload(artifact_paths.get("placebo_test_report_path")).get("null_exceedance_ratio"),
            policy.max_null_exceedance_ratio,
            "lte",
            artifact_paths.get("placebo_test_report_path"),
            required=policy.require_placebo,
        ),
        _metric_check(
            "regime_check",
            _payload(artifact_paths.get("regime_validation_report_path")).get("regime_pass_ratio"),
            policy.min_regime_pass_ratio,
            "gte",
            artifact_paths.get("regime_validation_report_path"),
        ),
        _report_required_check("sensitivity_check", False, artifact_paths.get("sensitivity_report_path")),
        _report_required_check("stress_backtest_check", policy.require_stress_backtest, artifact_paths.get("stress_backtest_report_path")),
        _pit_check(policy, artifact_paths),
        _leakage_check(policy, artifact_paths),
        _feature_promotion_check(artifact_paths),
        _alpha_lineage_check(policy, artifact_paths),
        _optional_report_check("cost_capacity_check", artifact_paths.get("capacity_report_path")),
        _optional_report_check("risk_control_check", artifact_paths.get("risk_control_report_path")),
        _optional_report_check("settlement_nav_check", artifact_paths.get("settlement_report_path")),
        _optional_report_check("eod_reconciliation_check", artifact_paths.get("eod_reconciliation_report_path")),
        _optional_report_check("model_lifecycle_check", artifact_paths.get("model_registry_record_path")),
    ]
    summary = {
        "passed_checks": sum(check.status == "passed" for check in checks),
        "warning_checks": sum(check.status == "warning" for check in checks),
        "failed_checks": sum(check.status == "failed" for check in checks),
        "skipped_checks": sum(check.status == "skipped" for check in checks),
        "blocker_count": sum(check.severity == "blocker" and check.status == "failed" for check in checks),
        "error_count": sum(check.severity == "error" and check.status == "failed" for check in checks),
        "warning_count": sum(check.severity == "warning" and check.status in {"failed", "warning"} for check in checks),
    }
    return FactorCertificationScorecard(factor_id=factor_id, policy_id=policy.policy_id, policy_profile=policy.profile_name, checks=checks, summary=summary)


def _metric_check(
    name: str,
    value: Any,
    threshold: float,
    direction: str,
    path: str | None,
    required: bool = True,
) -> FactorCertificationCheck:
    refs = {"artifact": path} if path else {}
    if value is None:
        return FactorCertificationCheck(
            name=name,
            status="failed" if required else "skipped",
            severity="error" if required else "info",
            threshold=threshold,
            reason="missing_metric",
            artifact_refs=refs,
        )
    numeric = float(value)
    passed = numeric >= threshold if direction == "gte" else numeric <= threshold
    return FactorCertificationCheck(
        name=name,
        status="passed" if passed else "failed",
        severity="error" if required else "warning",
        value=numeric,
        threshold=threshold,
        reason="" if passed else f"{direction}_threshold_not_met",
        artifact_refs=refs,
    )


def _report_required_check(name: str, required: bool, path: str | None) -> FactorCertificationCheck:
    exists = bool(path) and Path(path).exists()
    if exists:
        return FactorCertificationCheck(name, "passed", "info", True, True, artifact_refs={"artifact": str(path)})
    return FactorCertificationCheck(
        name,
        "failed" if required else "skipped",
        "error" if required else "info",
        False,
        True,
        "required_artifact_missing" if required else "artifact_not_provided",
    )


def _optional_report_check(name: str, path: str | None) -> FactorCertificationCheck:
    if path and Path(path).exists():
        return FactorCertificationCheck(name, "passed", "info", True, True, artifact_refs={"artifact": str(path)})
    return FactorCertificationCheck(name, "skipped", "info", False, True, "artifact_not_provided")


def _data_freeze_check(policy: CertificationPolicy, paths: dict[str, str | None]) -> FactorCertificationCheck:
    path = paths.get("data_version_manifest_path") or paths.get("research_data_freeze_path")
    return _report_required_check("data_freeze_check", policy.require_data_freeze, path)


def _pit_check(policy: CertificationPolicy, paths: dict[str, str | None]) -> FactorCertificationCheck:
    payload = _payload(paths.get("pit_validation_report_path"))
    if not payload:
        return _report_required_check("pit_check", policy.require_pit_passed, paths.get("pit_validation_report_path"))
    blockers = int(payload.get("blocker_count", 0) or 0)
    return FactorCertificationCheck("pit_check", "passed" if blockers == 0 else "failed", "error", blockers, 0, "pit_blockers" if blockers else "")


def _leakage_check(policy: CertificationPolicy, paths: dict[str, str | None]) -> FactorCertificationCheck:
    payload = _payload(paths.get("leakage_audit_report_path"))
    if not payload:
        return _report_required_check("leakage_check", policy.require_leakage_passed, paths.get("leakage_audit_report_path"))
    blockers = int(payload.get("blocker_count", 0) or 0)
    return FactorCertificationCheck("leakage_check", "passed" if blockers == 0 else "failed", "blocker", blockers, 0, "leakage_blockers" if blockers else "")


def _feature_promotion_check(paths: dict[str, str | None]) -> FactorCertificationCheck:
    validation_payload = _payload(paths.get("validation_lab_report_path"))
    target = validation_payload.get("target", {}) if validation_payload else {}
    metadata = target.get("metadata", {}) if isinstance(target, dict) else {}
    if metadata.get("unapproved_feature_used") or metadata.get("blocked_feature_used"):
        return FactorCertificationCheck(
            "feature_promotion_check",
            "failed",
            "blocker",
            True,
            False,
            "unapproved_or_blocked_feature_used",
            artifact_refs={"validation_lab_report": paths.get("validation_lab_report_path")},
        )
    if metadata.get("risk_filter_feature_used_as_alpha"):
        return FactorCertificationCheck(
            "feature_promotion_check",
            "failed",
            "error",
            True,
            False,
            "risk_filter_feature_used_as_alpha",
            artifact_refs={"validation_lab_report": paths.get("validation_lab_report_path")},
        )
    if paths.get("feature_promotion_allowlist_path") or metadata.get("feature_promotion_policy_hash"):
        return FactorCertificationCheck(
            "feature_promotion_check",
            "passed",
            "info",
            True,
            True,
            "",
            artifact_refs={
                "feature_promotion_allowlist": paths.get("feature_promotion_allowlist_path"),
                "validation_lab_report": paths.get("validation_lab_report_path"),
            },
        )
    return FactorCertificationCheck("feature_promotion_check", "skipped", "info", False, True, "promotion_artifact_not_provided")


def _alpha_lineage_check(policy: CertificationPolicy, paths: dict[str, str | None]) -> FactorCertificationCheck:
    return _report_required_check("alpha_lineage_check", policy.require_alpha_lineage, paths.get("alpha_factory_report_path"))


def _payload(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))
