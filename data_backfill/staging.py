"""Backfill staging, quarantine, and state helpers."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from artifact_schema.writer import utc_now, write_json_artifact, write_jsonl_artifact

from .models import BackfillJob, BackfillJobStatus, BackfillRunState


def load_backfill_state(path: str | Path, plan_id: str = "") -> BackfillRunState:
    target = Path(path)
    if not target.exists():
        return BackfillRunState(plan_id=plan_id, updated_at=utc_now(), jobs={})
    payload = json.loads(target.read_text(encoding="utf-8"))
    return BackfillRunState(
        plan_id=str(payload.get("plan_id") or plan_id),
        updated_at=str(payload.get("updated_at") or ""),
        jobs=dict(payload.get("jobs") or {}),
    )


def save_backfill_state(state: BackfillRunState, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json_artifact(target, state.to_dict(), "backfill_state", "data_backfill")
    return target


def mark_job(state: BackfillRunState, job: BackfillJob) -> BackfillRunState:
    jobs = dict(state.jobs)
    jobs[job.job_id] = job.to_dict()
    return BackfillRunState(plan_id=state.plan_id, updated_at=utc_now(), jobs=jobs)


def successful_job_ids(state: BackfillRunState) -> set[str]:
    return {job_id for job_id, payload in state.jobs.items() if payload.get("status") == BackfillJobStatus.success}


def write_staging_records(root: str | Path, job: BackfillJob, records: Iterable[Any]) -> tuple[Path, Path, int]:
    job_dir = Path(root) / "jobs" / job.job_id
    dataset_dir = job_dir / job.dataset
    payloads = [_to_jsonable(record) for record in records]
    records_path = write_jsonl_artifact(dataset_dir / "records.jsonl", payloads, "backfill_staging_records", "data_backfill")
    result_path = write_json_artifact(
        job_dir / "job_result.json",
        {**job.to_dict(), "records": len(payloads), "staging_path": str(records_path)},
        "backfill_job_result",
        "data_backfill",
    )
    return records_path, result_path, len(payloads)


def quarantine_job(staging_root: str | Path, quarantine_root: str | Path, job_id: str) -> Path | None:
    source = Path(staging_root) / "jobs" / job_id
    if not source.exists():
        return None
    target = Path(quarantine_root) / "jobs" / job_id
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return target


def _to_jsonable(record: Any) -> dict[str, Any]:
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)
    if hasattr(record, "to_dict"):
        return dict(record.to_dict())
    if isinstance(record, dict):
        return dict(record)
    raise TypeError(f"unsupported backfill record: {type(record)!r}")
