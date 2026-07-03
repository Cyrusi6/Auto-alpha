"""Artifact writers for backfill repair batches."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import BackfillRepairBatchPlan, BackfillRepairRunReport


def write_repair_artifacts(plan: BackfillRepairBatchPlan, report: BackfillRepairRunReport, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    plan_path = write_json_artifact(root / "repair_batch_plan.json", plan.to_dict(), "backfill_repair_batch_plan", "backfill_repair")
    plan_md = root / "repair_batch_plan.md"
    plan_md.write_text(_plan_md(plan), encoding="utf-8")
    report_path = write_json_artifact(root / "repair_run_report.json", report.to_dict(), "backfill_repair_run_report", "backfill_repair")
    report_md = root / "repair_run_report.md"
    report_md.write_text(_report_md(report), encoding="utf-8")
    results_path = write_jsonl_artifact(root / "repair_job_results.jsonl", [job.to_dict() for job in report.job_results], "backfill_repair_job_results", "backfill_repair")
    commands_path = root / "repair_commands.sh"
    commands_path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n\n" + "\n\n".join(job.command for job in plan.jobs) + "\n", encoding="utf-8")
    return {
        "repair_batch_plan_path": str(plan_path),
        "repair_batch_plan_md_path": str(plan_md),
        "repair_run_report_path": str(report_path),
        "repair_run_report_md_path": str(report_md),
        "repair_job_results_path": str(results_path),
        "repair_events_path": str(root / "repair_events.jsonl"),
        "repair_state_path": str(root / "repair_run_state.json"),
        "repair_commands_path": str(commands_path),
    }


def stdout_payload(plan: BackfillRepairBatchPlan, report: BackfillRepairRunReport, paths: dict[str, str]) -> dict:
    return {"status": report.status, "plan": plan.to_dict(), "summary": report.summary, "paths": paths}


def dumps(payload: dict, pretty: bool = False) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty)


def _plan_md(plan: BackfillRepairBatchPlan) -> str:
    lines = [
        "# Backfill Repair Batch Plan",
        "",
        f"- Batch: `{plan.repair_batch_id}`",
        f"- Jobs: {len(plan.jobs)}",
        f"- Data dir: `{plan.data_dir}`",
        "",
        "| Job | Dataset | Reason |",
        "| --- | --- | --- |",
    ]
    for job in plan.jobs:
        lines.append(f"| {job.repair_job_id} | {job.dataset} | {job.reason} |")
    if plan.warnings:
        lines.extend(["", "## Warnings", *[f"- {item}" for item in plan.warnings]])
    return "\n".join(lines) + "\n"


def _report_md(report: BackfillRepairRunReport) -> str:
    return "\n".join(
        [
            "# Backfill Repair Run Report",
            "",
            f"- Status: `{report.status}`",
            f"- Mode: `{report.mode}`",
            f"- Jobs: {report.summary.get('repair_job_count', 0)}",
            f"- Success: {report.summary.get('success_jobs', 0)}",
            f"- Blocked: {report.summary.get('blocked_jobs', 0)}",
            f"- Failed: {report.summary.get('failed_jobs', 0)}",
        ]
    ) + "\n"
