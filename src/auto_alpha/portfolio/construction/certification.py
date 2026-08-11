"""Portfolio certification policy, scorecard, decision, and reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class PortfolioCertificationStatus:
    certified = "certified"
    conditional = "conditional"
    rejected = "rejected"
    needs_review = "needs_review"
    insufficient_data = "insufficient_data"


@dataclass(frozen=True)
class PortfolioCertificationPolicy:
    policy_id: str
    profile_name: str
    require_portfolio_lab: bool = True
    require_factor_certification: bool = False
    min_selection_score: float = -999.0
    min_scenario_pass_ratio: float = 0.0
    min_successful_trial_count: int = 1
    min_fill_rate: float = 0.0
    max_constraint_reject_rate: float = 1.0
    max_avg_turnover: float = 1.0
    max_tracking_error: float = 1.0
    max_capacity_warning_count: int = 999
    max_risk_constraint_violations: float = 999.0
    allow_conditional: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioCertificationCheck:
    name: str
    status: str
    severity: str
    value: float | int | str | bool | None = None
    threshold: float | int | str | bool | None = None
    reason: str = ""
    artifact_refs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioCertificationScorecard:
    portfolio_policy_id: str
    factor_id: str
    policy_id: str
    policy_profile: str
    checks: list[PortfolioCertificationCheck]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checks"] = [check.to_dict() for check in self.checks]
        return payload


@dataclass(frozen=True)
class PortfolioCertificationDecision:
    portfolio_policy_id: str
    factor_id: str
    status: str
    passed: bool
    reasons: list[str]
    required_remediation: list[str]
    checks: dict[str, Any]
    policy_id: str
    policy_profile: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioCertificationPackage:
    portfolio_policy_id: str
    factor_id: str
    portfolio_policy: dict[str, Any]
    certification_policy: dict[str, Any]
    scorecard: dict[str, Any]
    decision: dict[str, Any]
    source_artifacts: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

from datetime import datetime



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

import hashlib
import json
from dataclasses import replace
from pathlib import Path



def portfolio_certification_policy_profile(profile_name: str = "sample_lenient_portfolio") -> PortfolioCertificationPolicy:
    if profile_name == "research_standard_portfolio":
        profile_name = "research_standard"
    if profile_name == "production_strict_portfolio":
        profile_name = "production_strict"
    if profile_name == "production_strict":
        return _with_hash(
            PortfolioCertificationPolicy(
                policy_id="",
                profile_name=profile_name,
                require_factor_certification=True,
                min_selection_score=-10.0,
                min_scenario_pass_ratio=0.75,
                min_successful_trial_count=2,
                min_fill_rate=0.5,
                max_constraint_reject_rate=0.5,
                max_avg_turnover=1.0,
                max_tracking_error=1.0,
                max_capacity_warning_count=10,
                max_risk_constraint_violations=10.0,
            )
        )
    if profile_name == "research_standard":
        return _with_hash(
            PortfolioCertificationPolicy(
                policy_id="",
                profile_name=profile_name,
                min_selection_score=-100.0,
                min_scenario_pass_ratio=0.5,
                min_successful_trial_count=1,
                min_fill_rate=0.0,
                max_constraint_reject_rate=1.0,
            )
        )
    return _with_hash(
        PortfolioCertificationPolicy(
            policy_id="",
            profile_name="sample_lenient_portfolio",
            require_factor_certification=False,
            min_selection_score=-999.0,
            min_scenario_pass_ratio=0.0,
            min_successful_trial_count=1,
            min_fill_rate=0.0,
            max_constraint_reject_rate=1.0,
            max_avg_turnover=999.0,
            max_tracking_error=999.0,
            max_capacity_warning_count=999,
            max_risk_constraint_violations=999.0,
        )
    )


def load_portfolio_certification_policy(
    path: str | Path | None = None,
    profile_name: str = "sample_lenient_portfolio",
) -> PortfolioCertificationPolicy:
    if path:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload.setdefault("policy_id", "")
        payload.setdefault("profile_name", profile_name)
        allowed = {key: value for key, value in payload.items() if key in PortfolioCertificationPolicy.__dataclass_fields__}
        return _with_hash(PortfolioCertificationPolicy(**allowed))
    return portfolio_certification_policy_profile(profile_name)


def portfolio_certification_policy_hash(policy: PortfolioCertificationPolicy) -> str:
    payload = {key: value for key, value in policy.to_dict().items() if key != "policy_id"}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _with_hash(policy: PortfolioCertificationPolicy) -> PortfolioCertificationPolicy:
    return replace(policy, policy_id=f"portfolio_cert_policy_{portfolio_certification_policy_hash(policy)}")

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact
from auto_alpha.portfolio.construction.optimizer import PortfolioPolicy



def write_portfolio_certification_artifacts(
    output_dir: str | Path,
    portfolio_policy: PortfolioPolicy,
    certification_policy: PortfolioCertificationPolicy,
    scorecard: PortfolioCertificationScorecard,
    decision: PortfolioCertificationDecision,
    package: PortfolioCertificationPackage,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    certified_policy = replace(
        portfolio_policy,
        certification_status=decision.status,
        certification_decision_path=str(root / "portfolio_certification_decision.json"),
    )
    activation_request = {
        "portfolio_policy_id": certified_policy.policy_id,
        "factor_id": decision.factor_id,
        "status": "forbidden_until_independent_shadow_audit",
        "requested_action": "independent_shadow_audit",
        "certification_status": decision.status,
        "shadow_only": True,
        "paper_ready": False,
        "live_ready": False,
    }
    paths = {
        "portfolio_certification_policy_path": root / "portfolio_certification_policy.json",
        "portfolio_certification_scorecard_path": root / "portfolio_certification_scorecard.json",
        "portfolio_certification_decision_path": root / "portfolio_certification_decision.json",
        "portfolio_certification_package_path": root / "portfolio_certification_package.json",
        "portfolio_certification_report_md_path": root / "portfolio_certification_report.md",
        "portfolio_certification_checks_path": root / "portfolio_certification_checks.jsonl",
        "certified_portfolio_policy_path": root / "certified_portfolio_policy.json",
        "portfolio_policy_activation_request_path": root / "portfolio_policy_activation_request.json",
    }
    write_json_artifact(paths["portfolio_certification_policy_path"], certification_policy.to_dict(), "portfolio_certification_policy", "portfolio_certification")
    write_json_artifact(paths["portfolio_certification_scorecard_path"], scorecard.to_dict(), "portfolio_certification_scorecard", "portfolio_certification")
    write_json_artifact(paths["portfolio_certification_decision_path"], decision.to_dict(), "portfolio_certification_decision", "portfolio_certification")
    write_json_artifact(paths["portfolio_certification_package_path"], package.to_dict(), "portfolio_certification_package", "portfolio_certification")
    write_jsonl_artifact(paths["portfolio_certification_checks_path"], [check.to_dict() for check in scorecard.checks], "portfolio_certification_checks", "portfolio_certification")
    write_json_artifact(paths["certified_portfolio_policy_path"], certified_policy.to_dict(), "certified_portfolio_policy", "portfolio_certification")
    write_json_artifact(paths["portfolio_policy_activation_request_path"], activation_request, "portfolio_policy_activation_request", "portfolio_certification")
    paths["portfolio_certification_report_md_path"].write_text(_markdown(certification_policy, scorecard, decision, certified_policy), encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def _markdown(
    policy: PortfolioCertificationPolicy,
    scorecard: PortfolioCertificationScorecard,
    decision: PortfolioCertificationDecision,
    portfolio_policy: PortfolioPolicy,
) -> str:
    lines = [
        "# Portfolio Certification Report",
        "",
        f"- portfolio_policy_id: `{decision.portfolio_policy_id}`",
        f"- factor_id: `{decision.factor_id}`",
        f"- status: `{decision.status}`",
        f"- policy_profile: `{policy.profile_name}`",
        "",
        "## Portfolio Policy",
        "",
        "```json",
        json.dumps(portfolio_policy.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Checks",
        "",
        "| check | status | severity | value | threshold | reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for check in scorecard.checks:
        lines.append(f"| {check.name} | {check.status} | {check.severity} | {check.value} | {check.threshold} | {check.reason} |")
    lines.extend(["", "Portfolio certification is a governance gate for local paper deployment, not a live trading instruction.", ""])
    return "\n".join(lines)

import json
from pathlib import Path
from typing import Any



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

import argparse
import json
from pathlib import Path
from typing import Any

from auto_alpha.portfolio.simulator.backtest import select_factor_id
from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.portfolio.construction.optimizer import load_portfolio_policy



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Certify portfolio optimizer policy artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in [
        "init-policy",
        "scorecard",
        "decide",
        "run",
        "register",
        "register-policy",
        "propose-activation",
        "create-activation-approval",
        "apply-approved-activation",
        "report",
        "smoke",
    ]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--factor-id")
    parser.add_argument("--factor-type", choices=["single", "composite", "any"], default="composite")
    parser.add_argument("--latest-approved", action="store_true")
    parser.add_argument("--portfolio-policy-path")
    parser.add_argument("--selected-policy-path")
    parser.add_argument("--selected-portfolio-policy-path")
    parser.add_argument("--portfolio-lab-report-path")
    parser.add_argument("--portfolio-robustness-report-path")
    parser.add_argument("--factor-certification-decision-path")
    parser.add_argument("--validation-lab-report-path")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--research-data-freeze-path")
    parser.add_argument("--pit-validation-report-path")
    parser.add_argument("--leakage-audit-report-path")
    parser.add_argument("--corporate-action-report-path")
    parser.add_argument("--settlement-report-path")
    parser.add_argument("--risk-control-report-path")
    parser.add_argument("--eod-reconciliation-report-path")
    parser.add_argument("--policy-path")
    parser.add_argument("--policy-profile", default="sample_lenient_portfolio")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--register-policy", action="store_true")
    parser.add_argument("--create-activation-approval", action="store_true")
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--approval-id")
    parser.add_argument("--actor", default="portfolio_policy_reviewer")
    parser.add_argument("--reason")
    parser.add_argument("--fail-on-rejected", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    if args.fail_on_rejected and payload.get("certification_status") in {"rejected", "insufficient_data"}:
        return 1
    return 0


def _run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    store = LocalFactorStore(args.factor_store_dir)
    if args.command in {
        "register",
        "register-policy",
        "propose-activation",
        "create-activation-approval",
        "apply-approved-activation",
    } or args.register_policy or args.create_activation_approval:
        raise ValueError("portfolio_activation_requires_independent_shadow_audit; legacy activation path disabled")
    factor_id = select_factor_id(store, args.factor_id, latest_approved=args.latest_approved or not args.factor_id, factor_type=args.factor_type)
    certification_policy = load_portfolio_certification_policy(args.policy_path, args.policy_profile)
    if args.command == "init-policy":
        path = output_dir / "portfolio_certification_policy.json"
        path.write_text(json.dumps(certification_policy.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return {"portfolio_certification_policy_path": str(path), "policy": certification_policy.to_dict()}
    policy_path = args.portfolio_policy_path or args.selected_portfolio_policy_path or args.selected_policy_path or _default_selected_policy_path(args)
    portfolio_policy = load_portfolio_policy(policy_path)
    if portfolio_policy is None:
        raise ValueError("portfolio_policy_path or selected_policy_path is required")
    artifact_paths = {
        "portfolio_lab_report_path": args.portfolio_lab_report_path,
        "portfolio_robustness_report_path": args.portfolio_robustness_report_path,
        "factor_certification_decision_path": args.factor_certification_decision_path,
        "validation_lab_report_path": args.validation_lab_report_path,
        "data_version_manifest_path": args.data_version_manifest_path,
        "research_data_freeze_path": args.research_data_freeze_path,
        "pit_validation_report_path": args.pit_validation_report_path,
        "leakage_audit_report_path": args.leakage_audit_report_path,
        "corporate_action_report_path": args.corporate_action_report_path,
        "settlement_report_path": args.settlement_report_path,
        "risk_control_report_path": args.risk_control_report_path,
        "eod_reconciliation_report_path": args.eod_reconciliation_report_path,
    }
    scorecard = build_portfolio_certification_scorecard(portfolio_policy.to_dict(), certification_policy, artifact_paths)
    decision = make_portfolio_certification_decision(scorecard, certification_policy)
    package = PortfolioCertificationPackage(
        portfolio_policy_id=portfolio_policy.policy_id,
        factor_id=factor_id,
        portfolio_policy=portfolio_policy.to_dict(),
        certification_policy=certification_policy.to_dict(),
        scorecard=scorecard.to_dict(),
        decision=decision.to_dict(),
        source_artifacts={key: value for key, value in artifact_paths.items() if value},
    )
    paths = write_portfolio_certification_artifacts(output_dir, portfolio_policy, certification_policy, scorecard, decision, package)
    model_version_id = None
    approval_id = None
    approval_status = None
    approval_id = None
    approval_status = None
    return {
        "factor_id": factor_id,
        "portfolio_policy_id": portfolio_policy.policy_id,
        "certification_status": decision.status,
        "certification_passed": decision.passed,
        "portfolio_certification_policy_profile": certification_policy.profile_name,
        "portfolio_certification_blocker_count": int(scorecard.summary.get("blocker_count", 0) or 0),
        "portfolio_certification_required_remediation_count": len(decision.required_remediation),
        "model_version_id": model_version_id,
        "approval_id": approval_id,
        "approval_status": approval_status,
        "scorecard_summary": scorecard.summary,
        "decision": decision.to_dict(),
        "paths": paths,
    }


def _apply_approved_activation(args: argparse.Namespace, store: LocalFactorStore) -> dict[str, Any]:
    raise ValueError("portfolio_activation_requires_independent_shadow_audit; legacy activation path disabled")


def _default_selected_policy_path(args: argparse.Namespace) -> str | None:
    lab_report = args.portfolio_lab_report_path
    if not lab_report:
        return None
    return str(Path(lab_report).parent / "selected_portfolio_policy.json")


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "PortfolioCertificationCheck",
    "PortfolioCertificationDecision",
    "PortfolioCertificationPackage",
    "PortfolioCertificationPolicy",
    "PortfolioCertificationScorecard",
    "build_portfolio_certification_scorecard",
    "load_portfolio_certification_policy",
    "make_portfolio_certification_decision",
    "portfolio_certification_policy_profile",
]
