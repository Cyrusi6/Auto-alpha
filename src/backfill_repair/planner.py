"""Build repair batches from observer and backfill artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import utc_now

from .models import BackfillRepairBatchPlan, BackfillRepairJob


REPAIRABLE_STATUSES = {"failed", "quarantined", "pending", "skipped"}


def build_repair_batch_plan(
    *,
    data_dir: str | Path,
    output_dir: str | Path,
    run_dir: str | Path | None = None,
    staging_dir: str | Path | None = None,
    repair_plan_path: str | Path | None = None,
    backfill_plan_path: str | Path | None = None,
    job_results_path: str | Path | None = None,
    state_path: str | Path | None = None,
    mode: str = "dry_run",
) -> BackfillRepairBatchPlan:
    repair_plan = _read_json(repair_plan_path)
    backfill_plan = _read_json(backfill_plan_path or _first_existing(run_dir, ["backfill_plan.json"]))
    job_rows = _read_jsonl(job_results_path or _first_existing(run_dir, ["backfill_job_results.jsonl"]))
    state_payload = _read_json(state_path or _first_existing(run_dir, ["backfill_state.json"]))
    commands = _repair_commands(repair_plan)
    jobs: list[BackfillRepairJob] = []
    for row in _repairable_rows(backfill_plan, job_rows, state_payload):
        dataset = str(row.get("dataset") or "unknown")
        source_job_id = str(row.get("job_id") or row.get("source_job_id") or "")
        reason = _reason_for_row(row)
        command = _command_for_dataset(dataset, commands) or str(row.get("command") or "")
        if not command:
            command = _fallback_command(dataset, data_dir, output_dir)
        jobs.append(
            BackfillRepairJob(
                repair_job_id=f"repair_{source_job_id or dataset}_{len(jobs):04d}",
                dataset=dataset,
                source_job_id=source_job_id or None,
                reason=reason,
                command=command,
                metadata={"source_status": row.get("status"), "source_error": row.get("error")},
            )
        )
    if not jobs and commands:
        for index, command in enumerate(commands):
            dataset = _dataset_from_command(command) or f"command_{index + 1}"
            jobs.append(
                BackfillRepairJob(
                    repair_job_id=f"repair_command_{index + 1:04d}",
                    dataset=dataset,
                    reason="observer_repair_command",
                    command=command,
                )
            )
    warnings = [] if jobs else ["No repair jobs were identified."]
    digest = hashlib.sha256(json.dumps([job.to_dict() for job in jobs], sort_keys=True).encode("utf-8")).hexdigest()
    return BackfillRepairBatchPlan(
        repair_batch_id=f"repair_batch_{digest[:16]}",
        generated_at=utc_now(),
        mode=mode,
        data_dir=str(data_dir),
        run_dir=str(run_dir) if run_dir else None,
        staging_dir=str(staging_dir) if staging_dir else None,
        jobs=jobs,
        warnings=warnings,
    )


def _repairable_rows(backfill_plan: dict[str, Any], job_rows: list[dict[str, Any]], state_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in job_rows:
        status = str(row.get("status") or "")
        if status in REPAIRABLE_STATUSES or _error_is_repairable(str(row.get("error") or "")):
            job_id = str(row.get("job_id") or "")
            if job_id and job_id not in seen:
                rows.append(row)
                seen.add(job_id)
    state_jobs = state_payload.get("jobs") if isinstance(state_payload.get("jobs"), dict) else {}
    for job_id, row in state_jobs.items():
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        if status in REPAIRABLE_STATUSES or _error_is_repairable(str(row.get("error") or "")):
            if job_id not in seen:
                rows.append({"job_id": job_id, **row})
                seen.add(job_id)
    for row in backfill_plan.get("jobs", []) if isinstance(backfill_plan.get("jobs"), list) else []:
        if not isinstance(row, dict):
            continue
        job_id = str(row.get("job_id") or "")
        if job_id and job_id not in seen and job_id not in state_jobs:
            rows.append({"status": "pending", "reason": "missing_state", **row})
            seen.add(job_id)
    return rows


def _repair_commands(repair_plan: dict[str, Any]) -> list[str]:
    payload = repair_plan.get("repair_plan") if isinstance(repair_plan.get("repair_plan"), dict) else repair_plan
    commands = payload.get("commands") if isinstance(payload, dict) else []
    return [str(command) for command in commands or [] if str(command).strip()]


def _command_for_dataset(dataset: str, commands: list[str]) -> str | None:
    needle = f"--datasets {dataset}"
    for command in commands:
        if needle in command or f"--trade-day-datasets {dataset}" in command or f"--ts-code-split-datasets {dataset}" in command:
            return command
    return commands[0] if len(commands) == 1 else None


def _dataset_from_command(command: str) -> str | None:
    parts = command.replace("\\\n", " ").split()
    for index, part in enumerate(parts):
        if part == "--datasets" and index + 1 < len(parts):
            return parts[index + 1].split(",")[0]
    return None


def _fallback_command(dataset: str, data_dir: str | Path, output_dir: str | Path) -> str:
    return (
        "uv run python -m data_backfill.run_backfill resume "
        "--provider sample "
        f"--data-dir {data_dir} "
        f"--output-dir {Path(output_dir) / 'repair_runs' / dataset} "
        f"--datasets {dataset} --mode append --resume --pretty"
    )


def _reason_for_row(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    error = str(row.get("error") or "")
    if status in {"failed", "quarantined"}:
        return status
    if "rate" in error.lower():
        return "rate_limit_interrupted"
    if "timeout" in error.lower():
        return "timeout"
    if status == "pending":
        return "missing_or_pending"
    if status == "skipped":
        return "skipped_needs_review"
    return "empty_or_needs_review"


def _error_is_repairable(error: str) -> bool:
    lower = error.lower()
    return any(token in lower for token in ("rate", "timeout", "empty", "permission", "network"))


def _first_existing(root: str | Path | None, names: list[str]) -> Path | None:
    if not root:
        return None
    for name in names:
        path = Path(root) / name
        if path.exists():
            return path
    return None


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path or not Path(path).exists():
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: str | Path | None) -> list[dict[str, Any]]:
    if not path or not Path(path).exists():
        return []
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows
