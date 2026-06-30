"""Matrix refresh artifact writers."""

from __future__ import annotations

from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import MatrixFreshnessReport, MatrixRefreshPlan, MatrixRefreshResult, MatrixSourceDiff


def write_matrix_refresh_artifacts(
    *,
    plan: MatrixRefreshPlan,
    source_diff: MatrixSourceDiff,
    freshness: MatrixFreshnessReport,
    result: MatrixRefreshResult,
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    plan_path = write_json_artifact(root / "matrix_refresh_plan.json", plan.to_dict(), "matrix_refresh_plan", "matrix_refresh")
    diff_path = write_json_artifact(root / "matrix_source_diff.json", source_diff.to_dict(), "matrix_source_diff", "matrix_refresh")
    issues_path = write_jsonl_artifact(root / "matrix_refresh_issues.jsonl", source_diff.issues + freshness.issues, "matrix_refresh_issues", "matrix_refresh")
    freshness_path = write_json_artifact(root / "matrix_freshness_report.json", freshness.to_dict(), "matrix_freshness_report", "matrix_refresh")
    result_path = write_json_artifact(root / "matrix_refresh_result.json", result.to_dict(), "matrix_refresh_result", "matrix_refresh")
    md_path = root / "matrix_refresh_plan.md"
    md_path.write_text(_render_plan_md(plan), encoding="utf-8")
    freshness_md = root / "matrix_freshness_report.md"
    freshness_md.write_text(_render_freshness_md(freshness), encoding="utf-8")
    return {
        "matrix_refresh_plan_path": str(plan_path),
        "matrix_refresh_plan_md_path": str(md_path),
        "matrix_source_diff_path": str(diff_path),
        "matrix_refresh_issues_path": str(issues_path),
        "matrix_freshness_report_path": str(freshness_path),
        "matrix_freshness_report_md_path": str(freshness_md),
        "matrix_refresh_result_path": str(result_path),
    }


def _render_plan_md(plan: MatrixRefreshPlan) -> str:
    return "\n".join(
        [
            "# Matrix Refresh Plan",
            "",
            f"- refresh_mode: `{plan.refresh_mode}`",
            f"- recommendation: `{plan.recommendation}`",
            f"- data_dir: `{plan.data_dir}`",
            f"- matrix_cache_dir: `{plan.matrix_cache_dir}`",
            f"- reasons: `{', '.join(plan.reasons)}`",
            "",
        ]
    )


def _render_freshness_md(report: MatrixFreshnessReport) -> str:
    lines = [
        "# Matrix Freshness Report",
        "",
        f"- status: `{report.status}`",
        f"- n_stocks: `{report.n_stocks}`",
        f"- n_dates: `{report.n_dates}`",
        "",
        "## Issues",
    ]
    for issue in report.issues:
        lines.append(f"- `{issue.get('severity')}` `{issue.get('code')}`: {issue.get('message')}")
    return "\n".join(lines) + "\n"
