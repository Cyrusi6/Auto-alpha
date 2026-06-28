"""Report writers for factor lifecycle governance."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import LifecycleEvaluationResult, LifecycleReport, ModelReviewPackage


def write_lifecycle_report(
    evaluation: LifecycleEvaluationResult,
    output_dir: str | Path,
    review_package: ModelReviewPackage | None = None,
    lineage_graph_path: str | None = None,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    report = LifecycleReport(
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        evaluation=evaluation.to_dict(),
        review_package_path=str(root / "model_review_package.json") if review_package else None,
        lineage_graph_path=lineage_graph_path,
    )
    paths = {
        "factor_lifecycle_report_path": str(root / "factor_lifecycle_report.json"),
        "factor_lifecycle_report_md_path": str(root / "factor_lifecycle_report.md"),
        "lifecycle_decisions_path": str(root / "lifecycle_decisions.jsonl"),
        "factor_health_checks_path": str(root / "factor_health_checks.jsonl"),
    }
    write_json_artifact(paths["factor_lifecycle_report_path"], report.to_dict(), artifact_type="factor_lifecycle_report", producer="factor_lifecycle")
    Path(paths["factor_lifecycle_report_md_path"]).write_text(_render_markdown(report, review_package), encoding="utf-8")
    write_jsonl_artifact(paths["lifecycle_decisions_path"], [evaluation.decision.to_dict()], artifact_type="lifecycle_decisions", producer="factor_lifecycle")
    write_jsonl_artifact(paths["factor_health_checks_path"], [check.to_dict() for check in evaluation.checks], artifact_type="factor_health_checks", producer="factor_lifecycle")
    if review_package:
        review_json = root / "model_review_package.json"
        review_md = root / "model_review_package.md"
        write_json_artifact(review_json, review_package.to_dict(), artifact_type="model_review_package", producer="factor_lifecycle")
        review_md.write_text(_render_review_markdown(review_package), encoding="utf-8")
        paths["model_review_package_path"] = str(review_json)
        paths["model_review_package_md_path"] = str(review_md)
    return paths


def _render_markdown(report: LifecycleReport, review_package: ModelReviewPackage | None) -> str:
    evaluation = report.evaluation
    decision = evaluation.get("decision", {})
    lines = [
        "# Factor Lifecycle Report",
        "",
        f"- factor_id: `{evaluation.get('factor_id')}`",
        f"- model_version_id: `{evaluation.get('model_version_id') or ''}`",
        f"- recommended_action: `{decision.get('recommended_action')}`",
        f"- severity: `{decision.get('severity')}`",
        "",
        "## Checks",
        "",
        "| name | severity | passed | value | threshold |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in evaluation.get("checks", []):
        lines.append(f"| {check.get('name')} | {check.get('severity')} | {check.get('passed')} | {check.get('value')} | {check.get('threshold')} |")
    if review_package:
        lines.extend(["", f"Review package: `{review_package.factor_id}`"])
    return "\n".join(lines) + "\n"


def _render_review_markdown(package: ModelReviewPackage) -> str:
    return "\n".join(
        [
            "# Model Review Package",
            "",
            f"- model_version_id: `{package.model_version_id or ''}`",
            f"- factor_id: `{package.factor_id}`",
            f"- lifecycle_status: `{package.lifecycle_status}`",
            f"- recommended_action: `{package.lifecycle_decision.get('recommended_action')}`",
            "",
            "## Reviewer Checklist",
            "",
            "| item | required | checked |",
            "| --- | --- | --- |",
            *[f"| {item['item']} | {item['required']} | {item['checked']} |" for item in package.reviewer_checklist],
            "",
            "## Health Summary",
            "",
            "```json",
            json.dumps(package.lifecycle_decision, ensure_ascii=False, indent=2),
            "```",
        ]
    )
