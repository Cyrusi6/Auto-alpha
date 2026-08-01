"""Writers for feature promotion artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact


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
