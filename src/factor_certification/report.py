"""Writers for factor certification artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import (
    CertificationPolicy,
    FactorCertificationDecision,
    FactorCertificationPackage,
    FactorCertificationScorecard,
)


def write_factor_certification_artifacts(
    output_dir: str | Path,
    policy: CertificationPolicy,
    scorecard: FactorCertificationScorecard,
    decision: FactorCertificationDecision,
    package: FactorCertificationPackage,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "factor_certification_policy_path": root / "factor_certification_policy.json",
        "factor_certification_scorecard_path": root / "factor_certification_scorecard.json",
        "factor_certification_decision_path": root / "factor_certification_decision.json",
        "factor_certification_package_path": root / "factor_certification_package.json",
        "factor_certification_report_md_path": root / "factor_certification_report.md",
        "factor_certification_checks_path": root / "factor_certification_checks.jsonl",
    }
    write_json_artifact(paths["factor_certification_policy_path"], policy.to_dict(), "factor_certification_policy", "factor_certification")
    write_json_artifact(paths["factor_certification_scorecard_path"], scorecard.to_dict(), "factor_certification_scorecard", "factor_certification")
    write_json_artifact(paths["factor_certification_decision_path"], decision.to_dict(), "factor_certification_decision", "factor_certification")
    write_json_artifact(paths["factor_certification_package_path"], package.to_dict(), "factor_certification_package", "factor_certification")
    write_jsonl_artifact(paths["factor_certification_checks_path"], [check.to_dict() for check in scorecard.checks], "factor_certification_checks", "factor_certification")
    paths["factor_certification_report_md_path"].write_text(_markdown(policy, scorecard, decision), encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def _markdown(policy: CertificationPolicy, scorecard: FactorCertificationScorecard, decision: FactorCertificationDecision) -> str:
    lines = [
        "# Factor Certification Report",
        "",
        f"- factor_id: `{decision.factor_id}`",
        f"- status: `{decision.status}`",
        f"- policy: `{policy.profile_name}`",
        "",
        "## Scorecard",
        "",
        "| check | status | severity | value | threshold | reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for check in scorecard.checks:
        lines.append(f"| {check.name} | {check.status} | {check.severity} | {check.value} | {check.threshold} | {check.reason} |")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "```json",
            json.dumps(decision.to_dict(), ensure_ascii=False, indent=2),
            "```",
            "",
            "Certification is a research governance artifact and does not guarantee future returns.",
            "",
        ]
    )
    return "\n".join(lines)
