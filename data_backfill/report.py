"""Backfill report writers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import BackfillPlan, BackfillQuotaSummary, BackfillReadinessReport, BackfillRunReport
from .planner import write_backfill_plan


def write_backfill_run_report(report: BackfillRunReport, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    run_path = write_json_artifact(root / "backfill_run_report.json", payload, "backfill_run_report", "data_backfill")
    jobs_path = write_jsonl_artifact(root / "backfill_job_results.jsonl", [job.to_dict() for job in report.jobs], "backfill_job_results", "data_backfill")
    quota_path = write_json_artifact(root / "backfill_quota_summary.json", report.quota.to_dict(), "backfill_quota_summary", "data_backfill")
    readiness = BackfillReadinessReport(
        provider=report.provider,
        status="ok" if report.quota.status == "ok" else "blocked",
        online_required=report.provider == "tushare",
        token_required=report.provider == "tushare",
        quota=report.quota,
        diagnostics=[] if report.quota.status == "ok" else [{"code": report.quota.reason, "severity": "warning"}],
    )
    readiness_path = write_json_artifact(root / "backfill_readiness_report.json", readiness.to_dict(), "backfill_readiness_report", "data_backfill")
    md_path = root / "backfill_run_report.md"
    md_path.write_text(_run_markdown(report), encoding="utf-8")
    return {
        "backfill_run_report_path": str(run_path),
        "backfill_run_report_md_path": str(md_path),
        "backfill_job_results_path": str(jobs_path),
        "backfill_quota_summary_path": str(quota_path),
        "backfill_readiness_report_path": str(readiness_path),
    }


def write_full_backfill_report(plan: BackfillPlan, report: BackfillRunReport, output_dir: str | Path) -> dict[str, str]:
    plan_json, plan_md = write_backfill_plan(plan, output_dir)
    paths = write_backfill_run_report(report, output_dir)
    paths.update({"backfill_plan_path": str(plan_json), "backfill_plan_md_path": str(plan_md)})
    return paths


def _run_markdown(report: BackfillRunReport) -> str:
    lines = [
        f"# Backfill Run {report.plan_id}",
        "",
        f"- Provider: {report.provider}",
        f"- Status: {report.status}",
        f"- Jobs: {len(report.jobs)}",
        f"- Success: {report.summary.get('success_jobs', 0)}",
        f"- Failed: {report.summary.get('failed_jobs', 0)}",
        f"- Skipped: {report.summary.get('skipped_jobs', 0)}",
        "",
        "| Dataset | Job | Status | Records | Error |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for job in report.jobs:
        lines.append(f"| {job.dataset} | {job.job_id} | {job.status} | {job.records} | {job.error or ''} |")
    return "\n".join(lines) + "\n"


def quota_to_payload(quota: BackfillQuotaSummary) -> dict[str, Any]:
    return quota.to_dict()
