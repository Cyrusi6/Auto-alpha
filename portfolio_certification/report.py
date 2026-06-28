"""Writers for portfolio certification artifacts."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact
from portfolio_optimizer import PortfolioPolicy

from .models import (
    PortfolioCertificationDecision,
    PortfolioCertificationPackage,
    PortfolioCertificationPolicy,
    PortfolioCertificationScorecard,
)


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
        "status": "pending",
        "requested_action": "activate_optimizer_policy",
        "certification_status": decision.status,
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
