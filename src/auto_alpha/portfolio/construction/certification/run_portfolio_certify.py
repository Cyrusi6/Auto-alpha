"""CLI for portfolio policy certification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from auto_alpha.portfolio.simulation.backtest.io import select_factor_id
from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.portfolio.construction.optimizer import load_portfolio_policy

from .decision import make_portfolio_certification_decision
from .models import PortfolioCertificationPackage
from .policy import load_portfolio_certification_policy
from .report import write_portfolio_certification_artifacts
from .scorecard import build_portfolio_certification_scorecard


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
