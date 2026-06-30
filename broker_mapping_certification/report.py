"""Report writer for broker mapping certification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import BrokerMappingCertificationPackage


def write_mapping_certification_report(package: BrokerMappingCertificationPackage, output_dir: str | Path) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    policy_path = target / "broker_mapping_certification_policy.json"
    scorecard_path = target / "broker_mapping_certification_scorecard.json"
    decision_path = target / "broker_mapping_certification_decision.json"
    package_path = target / "broker_mapping_certification_package.json"
    report_md_path = target / "broker_mapping_certification_report.md"
    checks_path = target / "broker_mapping_certification_checks.jsonl"
    checks = [{"check": key, "value": value} for key, value in sorted(package.decision.checks.items())]
    write_json_artifact(policy_path, package.policy.to_dict(), artifact_type="broker_mapping_certification_policy", producer="broker_mapping_certification")
    write_json_artifact(scorecard_path, {"certification_id": package.certification_id, "checks": package.decision.checks}, artifact_type="broker_mapping_certification_scorecard", producer="broker_mapping_certification")
    write_json_artifact(decision_path, package.decision.to_dict(), artifact_type="broker_mapping_certification_decision", producer="broker_mapping_certification")
    write_json_artifact(package_path, package.to_dict(), artifact_type="broker_mapping_certification_package", producer="broker_mapping_certification")
    write_jsonl_artifact(checks_path, checks, artifact_type="broker_mapping_certification_checks", producer="broker_mapping_certification")
    report_md_path.write_text(_markdown(package), encoding="utf-8")
    return {
        "policy_path": str(policy_path),
        "scorecard_path": str(scorecard_path),
        "decision_path": str(decision_path),
        "package_path": str(package_path),
        "report_md_path": str(report_md_path),
        "checks_path": str(checks_path),
    }


def _markdown(package: BrokerMappingCertificationPackage) -> str:
    decision = package.decision
    lines = [
        "# Broker Mapping Certification",
        "",
        f"- certification_id: `{decision.certification_id}`",
        f"- status: `{decision.status}`",
        f"- profile_id: `{decision.profile_id}`",
        f"- schema_name: `{decision.schema_name}`",
        f"- policy: `{decision.policy_name}`",
        "",
        "## Reasons",
    ]
    lines.extend([f"- {reason}" for reason in decision.reasons] or ["- none"])
    if decision.qmt_skeleton_notice:
        lines.extend(["", "## Skeleton Notice", decision.qmt_skeleton_notice])
    return "\n".join(lines) + "\n"
