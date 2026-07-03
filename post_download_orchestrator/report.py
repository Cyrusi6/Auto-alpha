"""Writers for post-download orchestration plans."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import utc_now, write_json_artifact, write_jsonl_artifact

from .models import PostDownloadPlan, PostDownloadRunReport


def build_run_report(plan: PostDownloadPlan, mode: str, status: str) -> PostDownloadRunReport:
    now = utc_now()
    return PostDownloadRunReport(
        run_id=f"post_download_run_{now.replace(':', '').replace('-', '')}",
        created_at=now,
        mode=mode,
        status=status,
        plan=plan,
        executed_steps=[],
        summary={
            "post_download_next_step": plan.next_step,
            "post_download_blocker_count": len(plan.blockers),
            "blocked_step_count": sum(1 for step in plan.steps if step.blocked),
            "readiness_status": plan.readiness_status,
        },
    )


def write_post_download_artifacts(plan: PostDownloadPlan, run_report: PostDownloadRunReport, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    plan_path = write_json_artifact(root / "post_download_plan.json", plan.to_dict(), "post_download_plan", "post_download_orchestrator")
    steps_path = write_jsonl_artifact(root / "post_download_steps.jsonl", [step.to_dict() for step in plan.steps], "post_download_steps", "post_download_orchestrator")
    report_path = write_json_artifact(root / "post_download_run_report.json", run_report.to_dict(), "post_download_run_report", "post_download_orchestrator")
    commands_path = root / "post_download_commands.sh"
    commands_path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n\n" + "\n\n".join(step.command for step in plan.steps if not step.blocked) + "\n", encoding="utf-8")
    plan_md = root / "post_download_plan.md"
    plan_md.write_text(_plan_markdown(plan), encoding="utf-8")
    report_md = root / "post_download_run_report.md"
    report_md.write_text(_report_markdown(run_report), encoding="utf-8")
    return {
        "post_download_plan_path": str(plan_path),
        "post_download_plan_md_path": str(plan_md),
        "post_download_steps_path": str(steps_path),
        "post_download_run_report_path": str(report_path),
        "post_download_run_report_md_path": str(report_md),
        "post_download_commands_path": str(commands_path),
    }


def stdout_payload(plan: PostDownloadPlan, run_report: PostDownloadRunReport, paths: dict[str, str] | None = None) -> dict:
    return {"status": run_report.status, "plan": plan.to_dict(), "summary": run_report.summary, "paths": paths or {}}


def dumps(payload: dict, pretty: bool = False) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty)


def _plan_markdown(plan: PostDownloadPlan) -> str:
    lines = [
        "# Post Download Plan",
        "",
        f"- Readiness status: `{plan.readiness_status}`",
        f"- Blockers: {len(plan.blockers)}",
        f"- Next step: `{plan.next_step or ''}`",
        "",
        "| Step | Status | Blocked | Description |",
        "| --- | --- | --- | --- |",
    ]
    for step in plan.steps:
        lines.append(f"| {step.step_id} | {step.status} | {step.blocked} | {step.description} |")
    lines.extend(["", "## Blockers"])
    lines.extend(f"- {item}" for item in plan.blockers)
    lines.extend(["", "## Commands", "", "```bash", "\n\n".join(step.command for step in plan.steps if not step.blocked), "```"])
    return "\n".join(lines) + "\n"


def _report_markdown(report: PostDownloadRunReport) -> str:
    return "\n".join(
        [
            "# Post Download Run Report",
            "",
            f"- Mode: `{report.mode}`",
            f"- Status: `{report.status}`",
            f"- Readiness status: `{report.plan.readiness_status}`",
            f"- Blockers: {len(report.plan.blockers)}",
        ]
    ) + "\n"
