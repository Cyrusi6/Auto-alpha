"""Writers for semantic data quality artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import DataQualityIssue, DataQualityLabReport


def write_data_quality_lab_report(
    report: DataQualityLabReport,
    issues: list[DataQualityIssue],
    repair_suggestions: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "data_quality_lab_report_path": str(
            write_json_artifact(root / "data_quality_lab_report.json", report.to_dict(), "data_quality_lab_report", "data_quality_lab")
        ),
        "data_quality_scorecard_path": str(
            write_json_artifact(root / "data_quality_scorecard.json", report.scorecard.to_dict(), "data_quality_scorecard", "data_quality_lab")
        ),
        "data_quality_rules_path": str(
            write_json_artifact(root / "data_quality_rules.json", {"rules": rules, "rule_count": len(rules)}, "data_quality_rules", "data_quality_lab")
        ),
        "data_quality_issues_path": str(
            write_jsonl_artifact(root / "data_quality_issues.jsonl", [issue.to_dict() for issue in issues], "data_quality_issues", "data_quality_lab")
        ),
        "dataset_quality_summary_path": str(
            write_jsonl_artifact(
                root / "dataset_quality_summary.jsonl",
                [item.to_dict() for item in report.scorecard.dataset_summaries],
                "dataset_quality_summary",
                "data_quality_lab",
            )
        ),
        "cross_dataset_quality_report_path": str(
            write_json_artifact(
                root / "cross_dataset_quality_report.json",
                report.cross_dataset_report,
                "cross_dataset_quality_report",
                "data_quality_lab",
            )
        ),
        "data_quality_repair_suggestions_path": str(
            write_jsonl_artifact(
                root / "data_quality_repair_suggestions.jsonl",
                repair_suggestions,
                "data_quality_repair_suggestions",
                "data_quality_lab",
            )
        ),
        "data_quality_freeze_gate_path": str(
            write_json_artifact(root / "data_quality_freeze_gate.json", report.freeze_gate.to_dict(), "data_quality_freeze_gate", "data_quality_lab")
        ),
    }
    (root / "data_quality_lab_report.md").write_text(_markdown(report, issues, repair_suggestions), encoding="utf-8")
    return paths


def stdout_payload(report: DataQualityLabReport, paths: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "status": report.status,
        "summary": report.summary,
        "freeze_gate": report.freeze_gate.to_dict(),
        "paths": paths or report.paths,
    }


def dumps(payload: dict[str, Any], pretty: bool = False) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty)


def _markdown(report: DataQualityLabReport, issues: list[DataQualityIssue], repair_suggestions: list[dict[str, Any]]) -> str:
    lines = [
        "# Data Quality Lab Report",
        "",
        f"- Status: `{report.status}`",
        f"- Data dir: `{report.data_dir}`",
        f"- Can create freeze: `{report.freeze_gate.can_create_freeze}`",
        f"- Can build matrix: `{report.freeze_gate.can_build_matrix}`",
        f"- Can run core alpha: `{report.freeze_gate.can_run_core_alpha}`",
        f"- Can run expanded alpha: `{report.freeze_gate.can_run_expanded_alpha}`",
        f"- Issues: {report.scorecard.issue_count}",
        f"- Errors: {report.scorecard.error_count}",
        f"- Warnings: {report.scorecard.warning_count}",
        "",
        "## Dataset Summary",
        "",
        "| Dataset | Status | Records | Issues | Errors | Warnings |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for item in report.scorecard.dataset_summaries:
        lines.append(
            f"| {item.dataset} | {item.status} | {item.record_count} | {item.issue_count} | {item.error_count} | {item.warning_count} |"
        )
    lines.extend(["", "## Issue Samples", "", "| Severity | Dataset | Rule | Message |", "| --- | --- | --- | --- |"])
    for issue in issues[:25]:
        lines.append(f"| {issue.severity} | {issue.dataset} | {issue.rule_id} | {issue.message} |")
    lines.extend(["", "## Repair Suggestions"])
    for item in repair_suggestions[:25]:
        lines.append(f"- `{item.get('action')}` for `{item.get('dataset')}`: {item.get('reason')}")
    return "\n".join(lines) + "\n"
