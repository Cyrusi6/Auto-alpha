"""Backfill observer report builder and writers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from artifact_schema.writer import utc_now, write_json_artifact, write_jsonl_artifact

from .eta import estimate_eta
from .models import BackfillObserverReport
from .postprocess import build_postprocess_plan
from .progress import build_progress_report
from .repair import build_repair_plan


def build_observer_report(
    run_dir: str | Path | None,
    data_dir: str | Path,
    staging_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    logs_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    profile_name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    datasets: Sequence[str] | None = None,
    index_codes: Sequence[str] | None = None,
    rate_limit_per_minute: int = 150,
    expected_trade_days: int | None = None,
    expected_security_count: int | None = None,
    env_file_name: str = ".env.local",
) -> BackfillObserverReport:
    observed, progress, issues, summary = build_progress_report(
        run_dir=run_dir,
        data_dir=data_dir,
        staging_dir=staging_dir,
        cache_dir=cache_dir,
        logs_dir=logs_dir,
        datasets=datasets,
        expected_trade_days=expected_trade_days,
        expected_security_count=expected_security_count,
        index_codes=index_codes,
    )
    eta = estimate_eta(progress, rate_limit_per_minute=rate_limit_per_minute)
    repair = build_repair_plan(
        progress,
        data_dir=data_dir,
        output_dir=Path(output_dir) / "repair_runs" if output_dir else None,
        start_date=start_date,
        end_date=end_date,
        index_codes=list(index_codes or []),
        rate_limit_per_minute=rate_limit_per_minute,
        env_file_name=env_file_name,
    )
    postprocess = build_postprocess_plan(progress, data_dir=data_dir, output_dir=Path(output_dir) / "postprocess" if output_dir else None, profile_name=profile_name)
    now = utc_now()
    summary = {
        **summary,
        "active_backfill_dataset": observed.active_dataset,
        "backfill_progress_ratio": summary.get("progress_ratio", 0.0),
        "backfill_remaining_jobs": eta.remaining_jobs,
        "backfill_eta_minutes": eta.estimated_remaining_minutes,
        "backfill_failed_jobs": summary.get("failed_jobs", 0),
        "backfill_quarantined_jobs": summary.get("quarantined_jobs", 0),
        "postprocess_blocker_count": len(postprocess.blockers),
    }
    return BackfillObserverReport(
        report_id=f"backfill_observer_{now.replace(':', '').replace('-', '')}",
        observed_at=now,
        observed_run=observed,
        datasets=progress,
        eta=eta,
        repair_plan=repair,
        postprocess_plan=postprocess,
        issues=issues,
        summary=summary,
    )


def write_observer_artifacts(report: BackfillObserverReport, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    report_path = write_json_artifact(root / "backfill_observer_report.json", report.to_dict(), "backfill_observer_report", "backfill_observer")
    progress_path = write_jsonl_artifact(root / "backfill_dataset_progress.jsonl", [item.to_dict() for item in report.datasets], "backfill_dataset_progress", "backfill_observer")
    eta_path = write_json_artifact(root / "backfill_eta_report.json", report.eta.to_dict(), "backfill_eta_report", "backfill_observer")
    repair_path = write_json_artifact(root / "backfill_repair_plan.json", report.repair_plan.to_dict(), "backfill_repair_plan", "backfill_observer")
    postprocess_path = write_json_artifact(root / "backfill_postprocess_plan.json", report.postprocess_plan.to_dict(), "backfill_postprocess_plan", "backfill_observer")
    issues_path = write_jsonl_artifact(root / "backfill_observer_issues.jsonl", [item.to_dict() for item in report.issues], "backfill_observer_issues", "backfill_observer")
    repair_md = root / "backfill_repair_plan.md"
    repair_md.write_text(_repair_markdown(report), encoding="utf-8")
    repair_sh = root / "backfill_repair_commands.sh"
    repair_sh.write_text("#!/usr/bin/env bash\nset -euo pipefail\n\n" + "\n\n".join(report.repair_plan.commands) + "\n", encoding="utf-8")
    post_md = root / "backfill_postprocess_plan.md"
    post_md.write_text(_postprocess_markdown(report), encoding="utf-8")
    post_sh = root / "backfill_postprocess_commands.sh"
    post_sh.write_text("#!/usr/bin/env bash\nset -euo pipefail\n\n" + "\n\n".join(report.postprocess_plan.commands) + "\n", encoding="utf-8")
    report_md = root / "backfill_observer_report.md"
    report_md.write_text(_report_markdown(report), encoding="utf-8")
    return {
        "backfill_observer_report_path": str(report_path),
        "backfill_observer_report_md_path": str(report_md),
        "backfill_dataset_progress_path": str(progress_path),
        "backfill_eta_report_path": str(eta_path),
        "backfill_repair_plan_path": str(repair_path),
        "backfill_repair_plan_md_path": str(repair_md),
        "backfill_repair_commands_path": str(repair_sh),
        "backfill_postprocess_plan_path": str(postprocess_path),
        "backfill_postprocess_plan_md_path": str(post_md),
        "backfill_postprocess_commands_path": str(post_sh),
        "backfill_observer_issues_path": str(issues_path),
    }


def _report_markdown(report: BackfillObserverReport) -> str:
    lines = [
        "# Backfill Observer Report",
        "",
        f"- Observed at: `{report.observed_at}`",
        f"- Run status: `{report.observed_run.status}`",
        f"- Active dataset: `{report.observed_run.active_dataset or ''}`",
        f"- Progress: {float(report.summary.get('progress_ratio', 0.0) or 0.0):.2%}",
        f"- Remaining jobs: {report.eta.remaining_jobs}",
        f"- ETA minutes: {report.eta.estimated_remaining_minutes}",
        "",
        "| Dataset | Progress | Jobs | Failed | Pending | Records | Size bytes |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report.datasets:
        lines.append(
            f"| {item.dataset} | {item.progress_ratio:.2%} | {item.total_jobs} | {item.failed_jobs} | {item.pending_jobs} | {item.records} | {item.size_bytes} |"
        )
    lines.extend(["", "## Issues", "", "| Severity | Code | Dataset | Message |", "| --- | --- | --- | --- |"])
    for issue in report.issues:
        lines.append(f"| {issue.severity} | {issue.code} | {issue.dataset or ''} | {issue.message} |")
    return "\n".join(lines) + "\n"


def _repair_markdown(report: BackfillObserverReport) -> str:
    return "\n".join(
        [
            "# Backfill Repair Plan",
            "",
            f"- Failed jobs: {report.repair_plan.failed_jobs}",
            f"- Missing jobs: {report.repair_plan.missing_jobs}",
            f"- Empty expected jobs: {report.repair_plan.empty_but_expected_jobs}",
            "",
            "```bash",
            "\n\n".join(report.repair_plan.commands),
            "```",
        ]
    ) + "\n"


def _postprocess_markdown(report: BackfillObserverReport) -> str:
    lines = [
        "# Backfill Postprocess Plan",
        "",
        f"- Blockers: {len(report.postprocess_plan.blockers)}",
        "",
        "## Blockers",
    ]
    lines.extend(f"- {item}" for item in report.postprocess_plan.blockers)
    lines.extend(["", "## Commands", "", "```bash", "\n\n".join(report.postprocess_plan.commands), "```"])
    return "\n".join(lines) + "\n"


def stdout_payload(report: BackfillObserverReport, paths: dict[str, str] | None = None) -> dict:
    return {
        "status": report.observed_run.status,
        "active_dataset": report.observed_run.active_dataset,
        "summary": report.summary,
        "eta": report.eta.to_dict(),
        "paths": paths or {},
        "issues": [item.to_dict() for item in report.issues[:20]],
    }


def dumps(payload: dict, pretty: bool = False) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty)
