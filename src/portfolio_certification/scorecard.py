"""Portfolio certification scorecard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import PortfolioCertificationCheck, PortfolioCertificationPolicy, PortfolioCertificationScorecard


def build_portfolio_certification_scorecard(
    portfolio_policy: dict[str, Any],
    certification_policy: PortfolioCertificationPolicy,
    artifact_paths: dict[str, str | None],
) -> PortfolioCertificationScorecard:
    lab = _payload(artifact_paths.get("portfolio_lab_report_path"))
    robustness = _payload(artifact_paths.get("portfolio_robustness_report_path"))
    factor_cert = _payload(artifact_paths.get("factor_certification_decision_path"))
    selected_id = str(portfolio_policy.get("policy_id") or lab.get("summary", {}).get("selected_policy_id") or "")
    factor_id = str(portfolio_policy.get("source_factor_id") or lab.get("factor_id") or "")
    ranked = robustness.get("ranked_policies", []) if isinstance(robustness.get("ranked_policies"), list) else []
    selected_row = next((row for row in ranked if row.get("policy_id") == selected_id), ranked[0] if ranked else {})
    checks = [
        _report_required_check("portfolio_lab_check", certification_policy.require_portfolio_lab, artifact_paths.get("portfolio_lab_report_path")),
        _metric_check("selection_score_check", selected_row.get("selection_score"), certification_policy.min_selection_score, "gte", artifact_paths.get("portfolio_robustness_report_path")),
        _metric_check("scenario_pass_ratio_check", selected_row.get("scenario_pass_ratio"), certification_policy.min_scenario_pass_ratio, "gte", artifact_paths.get("portfolio_robustness_report_path")),
        _metric_check("successful_trial_count_check", selected_row.get("successful_trials"), certification_policy.min_successful_trial_count, "gte", artifact_paths.get("portfolio_robustness_report_path")),
        _metric_check("turnover_check", selected_row.get("avg_turnover"), certification_policy.max_avg_turnover, "lte", artifact_paths.get("portfolio_robustness_report_path")),
        _metric_check("constraint_reject_check", selected_row.get("avg_reject_rate"), certification_policy.max_constraint_reject_rate, "lte", artifact_paths.get("portfolio_robustness_report_path")),
        _metric_check("capacity_warning_check", selected_row.get("capacity_warning_count", 0), certification_policy.max_capacity_warning_count, "lte", artifact_paths.get("portfolio_robustness_report_path"), required=False),
        _factor_certification_check(certification_policy, factor_cert, artifact_paths.get("factor_certification_decision_path")),
    ]
    summary = {
        "passed_checks": sum(check.status == "passed" for check in checks),
        "warning_checks": sum(check.status == "warning" for check in checks),
        "failed_checks": sum(check.status == "failed" for check in checks),
        "skipped_checks": sum(check.status == "skipped" for check in checks),
        "blocker_count": sum(check.severity == "blocker" and check.status == "failed" for check in checks),
        "error_count": sum(check.severity == "error" and check.status == "failed" for check in checks),
        "warning_count": sum(check.severity == "warning" and check.status in {"failed", "warning"} for check in checks),
        "selected_policy_id": selected_id,
        "selection_score": float(selected_row.get("selection_score", -999.0) or -999.0),
    }
    return PortfolioCertificationScorecard(
        portfolio_policy_id=selected_id,
        factor_id=factor_id,
        policy_id=certification_policy.policy_id,
        policy_profile=certification_policy.profile_name,
        checks=checks,
        summary=summary,
    )


def _metric_check(name: str, value: Any, threshold: float, direction: str, path: str | None, required: bool = True) -> PortfolioCertificationCheck:
    refs = {"artifact": path} if path else {}
    if value is None:
        return PortfolioCertificationCheck(name, "failed" if required else "skipped", "error" if required else "info", None, threshold, "missing_metric", refs)
    numeric = float(value)
    passed = numeric >= float(threshold) if direction == "gte" else numeric <= float(threshold)
    return PortfolioCertificationCheck(name, "passed" if passed else "failed", "error" if required else "warning", numeric, threshold, "" if passed else f"{direction}_threshold_not_met", refs)


def _report_required_check(name: str, required: bool, path: str | None) -> PortfolioCertificationCheck:
    exists = bool(path) and Path(path).exists()
    if exists:
        return PortfolioCertificationCheck(name, "passed", "info", True, True, artifact_refs={"artifact": str(path)})
    return PortfolioCertificationCheck(name, "failed" if required else "skipped", "error" if required else "info", False, True, "required_artifact_missing" if required else "artifact_not_provided")


def _factor_certification_check(policy: PortfolioCertificationPolicy, payload: dict[str, Any], path: str | None) -> PortfolioCertificationCheck:
    if not path or not Path(path).exists():
        return _report_required_check("factor_certification_check", policy.require_factor_certification, path)
    status = str(payload.get("certification_status") or payload.get("status") or "")
    passed = status in {"certified", "conditional"} or bool(payload.get("passed"))
    return PortfolioCertificationCheck("factor_certification_check", "passed" if passed else "failed", "error" if policy.require_factor_certification else "warning", status, "certified_or_conditional", "" if passed else "factor_certification_not_passed", {"artifact": str(path)})


def _payload(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))
