"""Writers for post-download orchestration plans."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import utc_now, write_json_artifact, write_jsonl_artifact

from .models import FreezeCandidatePackage, PostDownloadPlan, PostDownloadRunReport, PostDownloadState, PostDownloadStepRun


def build_run_report(
    plan: PostDownloadPlan,
    mode: str,
    status: str,
    step_runs: list[PostDownloadStepRun] | None = None,
    run_id: str | None = None,
) -> PostDownloadRunReport:
    now = utc_now()
    return PostDownloadRunReport(
        run_id=run_id or f"post_download_run_{now.replace(':', '').replace('-', '')}",
        created_at=now,
        mode=mode,
        status=status,
        plan=plan,
        executed_steps=[step.to_dict() for step in step_runs or []],
        summary={
            "post_download_next_step": plan.next_step,
            "post_download_blocker_count": len(plan.blockers),
            "blocked_step_count": sum(1 for step in plan.steps if step.blocked),
            "readiness_status": plan.readiness_status,
            "executed_step_count": len(step_runs or []),
            "failed_step_count": sum(1 for step in step_runs or [] if step.status == "failed"),
            "blocked_run_step_count": sum(1 for step in step_runs or [] if step.status == "blocked"),
        },
    )


def write_post_download_artifacts(
    plan: PostDownloadPlan,
    run_report: PostDownloadRunReport,
    output_dir: str | Path,
    *,
    step_runs: list[PostDownloadStepRun] | None = None,
    state: PostDownloadState | None = None,
    freeze_candidate: FreezeCandidatePackage | None = None,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    plan_path = write_json_artifact(root / "post_download_plan.json", plan.to_dict(), "post_download_plan", "post_download_orchestrator")
    steps_path = write_jsonl_artifact(root / "post_download_steps.jsonl", [step.to_dict() for step in plan.steps], "post_download_steps", "post_download_orchestrator")
    step_runs_path = write_jsonl_artifact(root / "post_download_step_runs.jsonl", [step.to_dict() for step in step_runs or []], "post_download_step_runs", "post_download_orchestrator")
    report_path = write_json_artifact(root / "post_download_run_report.json", run_report.to_dict(), "post_download_run_report", "post_download_orchestrator")
    state_path = write_json_artifact(root / "post_download_state.json", (state.to_dict() if state else {"run_id": run_report.run_id, "plan_id": plan.plan_id, "steps": {}}), "post_download_state", "post_download_orchestrator")
    events_path = root / "post_download_events.jsonl"
    events_path.touch(exist_ok=True)
    package_path = None
    package_md = None
    if freeze_candidate is not None:
        package_path = write_json_artifact(root / "freeze_candidate_package.json", freeze_candidate.to_dict(), "freeze_candidate_package", "post_download_orchestrator")
        package_md = root / "freeze_candidate_package.md"
        package_md.write_text(_freeze_candidate_markdown(freeze_candidate), encoding="utf-8")
    final_package = {
        "status": run_report.status,
        "run_id": run_report.run_id,
        "readiness_status": plan.readiness_status,
        "freeze_candidate_status": freeze_candidate.status if freeze_candidate else "",
        "post_download_run_report_path": str(report_path),
        "freeze_candidate_package_path": str(package_path) if package_path else None,
    }
    final_package_path = write_json_artifact(root / "post_download_final_package.json", final_package, "post_download_final_package", "post_download_orchestrator")
    catalog = {
        "artifact_count": 0,
        "artifacts": [],
    }
    commands_path = root / "post_download_commands.sh"
    commands_path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n\n" + "\n\n".join(step.command for step in plan.steps if not step.blocked) + "\n", encoding="utf-8")
    plan_md = root / "post_download_plan.md"
    plan_md.write_text(_plan_markdown(plan), encoding="utf-8")
    report_md = root / "post_download_run_report.md"
    report_md.write_text(_report_markdown(run_report), encoding="utf-8")
    paths = {
        "post_download_plan_path": str(plan_path),
        "post_download_plan_md_path": str(plan_md),
        "post_download_steps_path": str(steps_path),
        "post_download_step_runs_path": str(step_runs_path),
        "post_download_run_report_path": str(report_path),
        "post_download_run_report_md_path": str(report_md),
        "post_download_state_path": str(state_path),
        "post_download_events_path": str(events_path),
        "post_download_final_package_path": str(final_package_path),
        "post_download_commands_path": str(commands_path),
    }
    if package_path:
        paths["freeze_candidate_package_path"] = str(package_path)
    if package_md:
        paths["freeze_candidate_package_md_path"] = str(package_md)
    catalog["artifacts"] = [{"name": key, "path": value} for key, value in sorted(paths.items())]
    catalog["artifact_count"] = len(catalog["artifacts"])
    catalog_path = write_json_artifact(root / "post_download_artifact_catalog.json", catalog, "post_download_artifact_catalog", "post_download_orchestrator")
    paths["post_download_artifact_catalog_path"] = str(catalog_path)
    return paths


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


def _freeze_candidate_markdown(package: FreezeCandidatePackage) -> str:
    lines = [
        "# Freeze Candidate Package",
        "",
        f"- Status: `{package.status}`",
        f"- Data dir: `{package.data_dir}`",
        f"- Proposed freeze: `{package.proposed_freeze_name}`",
        "",
        "## Blockers",
    ]
    lines.extend(f"- {item}" for item in package.blockers)
    if package.warnings:
        lines.extend(["", "## Warnings", *[f"- {item}" for item in package.warnings]])
    return "\n".join(lines) + "\n"
