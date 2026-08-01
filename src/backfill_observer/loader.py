"""Read-only loaders for backfill artifacts and raw dataset files."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from artifact_schema.writer import utc_now


TERMINAL_STATUSES = {"success", "failed", "skipped", "resumed", "quarantined"}


def read_json(path: str | Path | None) -> tuple[dict[str, Any], list[str]]:
    if not path:
        return {}, ["missing path"]
    target = Path(path)
    if not target.exists():
        return {}, [f"missing file: {target}"]
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}, []
    except Exception as exc:
        return {}, [f"failed to read JSON {target}: {exc}"]


def iter_jsonl(path: str | Path | None) -> Iterable[dict[str, Any]]:
    if not path:
        return
    target = Path(path)
    if not target.exists():
        return
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def load_run_artifacts(run_dir: str | Path | None, logs_dir: str | Path | None = None) -> dict[str, Any]:
    root = Path(run_dir) if run_dir else None
    warnings: list[str] = []
    state, state_warnings = read_json(root / "backfill_state.json" if root else None)
    warnings.extend(state_warnings)
    plan, plan_warnings = read_json(root / "backfill_plan.json" if root else None)
    warnings.extend(plan_warnings)
    report, _ = read_json(root / "backfill_run_report.json" if root else None)
    jobs_path = root / "backfill_job_results.jsonl" if root else None
    job_results = list(iter_jsonl(jobs_path))
    events_path = root / "backfill_progress_events.jsonl" if root else None
    progress_events = list(iter_jsonl(events_path))
    active_from_logs = parse_active_log_dataset(logs_dir)
    return {
        "state": state,
        "plan": plan,
        "report": report,
        "job_results": job_results,
        "progress_events": progress_events,
        "warnings": warnings,
        "active_from_logs": active_from_logs,
        "loaded_at": utc_now(),
    }


def parse_active_log_dataset(logs_dir: str | Path | None) -> dict[str, str | None]:
    if not logs_dir:
        return {"active_dataset": None, "active_job_id": None, "latest_log_line": None}
    root = Path(logs_dir)
    if not root.exists():
        return {"active_dataset": None, "active_job_id": None, "latest_log_line": None}
    latest_line: str | None = None
    for path in sorted(root.glob("*.log"), key=lambda item: item.stat().st_mtime if item.exists() else 0):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines[-200:]:
            if "START " in line or "dataset" in line.lower():
                latest_line = line
    dataset = None
    if latest_line and "START " in latest_line:
        chunk = latest_line.split("START ", 1)[1].split()[0]
        dataset = chunk.strip() or None
    return {"active_dataset": dataset, "active_job_id": None, "latest_log_line": latest_line}


def dataset_records_path(data_dir: str | Path, dataset: str) -> Path:
    return Path(data_dir) / dataset / "records.jsonl"


def scan_dataset_file(data_dir: str | Path, dataset: str, date_field: str | None = None) -> dict[str, Any]:
    path = dataset_records_path(data_dir, dataset)
    if not path.exists():
        return {
            "records": 0,
            "size_bytes": 0,
            "first_date": None,
            "last_date": None,
            "ts_code_count": 0,
            "exists": False,
        }
    first_date: str | None = None
    last_date: str | None = None
    ts_codes: set[str] = set()
    records = 0
    for payload in iter_jsonl(path):
        records += 1
        if "ts_code" in payload and payload.get("ts_code"):
            ts_codes.add(str(payload.get("ts_code")))
        if date_field and payload.get(date_field):
            value = str(payload.get(date_field))
            first_date = value if first_date is None or value < first_date else first_date
            last_date = value if last_date is None or value > last_date else last_date
        elif payload.get("trade_date"):
            value = str(payload.get("trade_date"))
            first_date = value if first_date is None or value < first_date else first_date
            last_date = value if last_date is None or value > last_date else last_date
    return {
        "records": records,
        "size_bytes": path.stat().st_size,
        "first_date": first_date,
        "last_date": last_date,
        "ts_code_count": len(ts_codes),
        "exists": True,
    }


def jobs_from_state_and_results(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    state_jobs = artifacts.get("state", {}).get("jobs", {})
    if isinstance(state_jobs, dict):
        jobs.extend(dict(job) for job in state_jobs.values() if isinstance(job, dict))
    results = artifacts.get("job_results") or []
    if results:
        known = {str(job.get("job_id")) for job in jobs if job.get("job_id")}
        for item in results:
            if str(item.get("job_id")) not in known:
                jobs.append(dict(item))
    plan_jobs = artifacts.get("plan", {}).get("jobs", [])
    known = {str(job.get("job_id")) for job in jobs if job.get("job_id")}
    for item in plan_jobs if isinstance(plan_jobs, list) else []:
        if isinstance(item, dict) and str(item.get("job_id")) not in known:
            jobs.append({**item, "status": item.get("status", "pending")})
    return jobs
