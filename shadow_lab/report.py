"""Writers for shadow lab artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import ShadowLabReport


def write_shadow_lab_report(report: ShadowLabReport, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    report_path = write_json_artifact(root / "shadow_lab_report.json", payload, "shadow_lab_report", "shadow_lab")
    md_path = root / "shadow_lab_report.md"
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    days_path = write_jsonl_artifact(root / "shadow_day_summaries.jsonl", [item.to_dict() for item in report.day_summaries], "shadow_day_summaries", "shadow_lab")
    perf_path = write_jsonl_artifact(root / "shadow_performance_series.jsonl", [item.to_dict() for item in report.day_summaries], "shadow_performance_series", "shadow_lab")
    drift_series_path = write_jsonl_artifact(root / "shadow_drift_series.jsonl", [item.to_dict() for item in report.day_summaries], "shadow_drift_series", "shadow_lab")
    drift_path = write_json_artifact(root / "shadow_drift_summary.json", report.drift_summary, "shadow_drift_summary", "shadow_lab")
    suggestions_path = write_json_artifact(
        root / "shadow_calibration_suggestions.json",
        {"suggestions": report.calibration_suggestions},
        "shadow_calibration_suggestions",
        "shadow_lab",
    )
    issues_path = write_jsonl_artifact(root / "shadow_lab_issues.jsonl", [item.to_dict() for item in report.issues], "shadow_lab_issues", "shadow_lab")
    return {
        "shadow_lab_report_path": str(report_path),
        "shadow_lab_report_md_path": str(md_path),
        "shadow_day_summaries_path": str(days_path),
        "shadow_performance_series_path": str(perf_path),
        "shadow_drift_series_path": str(drift_series_path),
        "shadow_drift_summary_path": str(drift_path),
        "shadow_calibration_suggestions_path": str(suggestions_path),
        "shadow_lab_issues_path": str(issues_path),
    }


def _render_markdown(payload: dict) -> str:
    return "\n".join(
        [
            "# Shadow Lab Report",
            "",
            f"- status: `{payload.get('status')}`",
            f"- shadow_days: `{payload.get('performance_summary', {}).get('shadow_day_count', 0)}`",
            f"- cumulative_return: `{payload.get('performance_summary', {}).get('shadow_cumulative_return', 0.0)}`",
            f"- drift_breaches: `{payload.get('drift_summary', {}).get('shadow_drift_breach_count', 0)}`",
            "",
            "## Drift Summary",
            "",
            "```json",
            json.dumps(payload.get("drift_summary", {}), ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ]
    ) + "\n"
