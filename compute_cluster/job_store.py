"""JSON/JSONL job state store for local compute jobs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ComputeJobSpec, ComputeJobStatus, TERMINAL_STATUSES


class LocalComputeJobStore:
    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_path = self.state_dir / "compute_jobs.jsonl"
        self.state_path = self.state_dir / "compute_job_state.json"
        self.runs_path = self.state_dir / "compute_job_runs.jsonl"
        self.heartbeats_path = self.state_dir / "compute_heartbeats.jsonl"
        self.events_path = self.state_dir / "compute_scheduler_events.jsonl"

    def submit_jobs(self, jobs: list[ComputeJobSpec]) -> dict[str, int]:
        state = self.load_state()
        existing = set(state.get("jobs", {}).keys())
        submitted = 0
        skipped = 0
        with self.jobs_path.open("a", encoding="utf-8") as handle:
            for job in jobs:
                if job.job_id in existing:
                    skipped += 1
                    continue
                row = job.to_dict()
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                state.setdefault("jobs", {})[job.job_id] = {
                    "status": ComputeJobStatus.PENDING,
                    "attempts": 0,
                    "submitted_at": _utc_now(),
                    "updated_at": _utc_now(),
                }
                existing.add(job.job_id)
                submitted += 1
        self.save_state(state)
        self.append_event("submit_jobs", {"submitted": submitted, "skipped_existing": skipped})
        return {"submitted": submitted, "skipped_existing": skipped}

    def list_jobs(self) -> list[ComputeJobSpec]:
        jobs: list[ComputeJobSpec] = []
        for row in _read_jsonl(self.jobs_path):
            jobs.append(ComputeJobSpec(**_job_payload(row)))
        return jobs

    def get_job(self, job_id: str) -> ComputeJobSpec | None:
        for job in self.list_jobs():
            if job.job_id == job_id:
                return job
        return None

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"jobs": {}}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"jobs": {}}

    def save_state(self, state: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.state_path)

    def job_status(self, job_id: str) -> str:
        return str(self.load_state().get("jobs", {}).get(job_id, {}).get("status", ComputeJobStatus.PENDING))

    def attempts(self, job_id: str) -> int:
        return int(self.load_state().get("jobs", {}).get(job_id, {}).get("attempts", 0) or 0)

    def update_status(self, job_id: str, status: str, **metadata: Any) -> None:
        state = self.load_state()
        row = state.setdefault("jobs", {}).setdefault(job_id, {"attempts": 0})
        row["status"] = status
        row["updated_at"] = _utc_now()
        row.update(metadata)
        self.save_state(state)
        self.append_event("status", {"job_id": job_id, "status": status, **metadata})

    def increment_attempt(self, job_id: str) -> int:
        state = self.load_state()
        row = state.setdefault("jobs", {}).setdefault(job_id, {"status": ComputeJobStatus.PENDING, "attempts": 0})
        row["attempts"] = int(row.get("attempts", 0) or 0) + 1
        row["updated_at"] = _utc_now()
        self.save_state(state)
        return int(row["attempts"])

    def dependencies_satisfied(self, job: ComputeJobSpec) -> bool:
        state = self.load_state().get("jobs", {})
        return all(state.get(dep, {}).get("status") == ComputeJobStatus.SUCCESS for dep in job.dependencies)

    def runnable_jobs(self, resume: bool = False) -> list[ComputeJobSpec]:
        state = self.load_state().get("jobs", {})
        jobs = []
        for job in self.list_jobs():
            status = state.get(job.job_id, {}).get("status", ComputeJobStatus.PENDING)
            if status in {ComputeJobStatus.PENDING, ComputeJobStatus.RESUMED}:
                jobs.append(job)
            elif resume and status in {ComputeJobStatus.FAILED, ComputeJobStatus.TIMED_OUT}:
                jobs.append(job)
        return sorted(jobs, key=lambda item: (-int(item.priority), item.job_id))

    def resume_pending_or_failed(self) -> int:
        state = self.load_state()
        count = 0
        for row in state.get("jobs", {}).values():
            if row.get("status") in {ComputeJobStatus.FAILED, ComputeJobStatus.TIMED_OUT, ComputeJobStatus.RUNNING, ComputeJobStatus.LEASED}:
                row["status"] = ComputeJobStatus.RESUMED
                row["updated_at"] = _utc_now()
                count += 1
        self.save_state(state)
        self.append_event("resume", {"resumed": count})
        return count

    def append_run(self, run: dict[str, Any]) -> None:
        with self.runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(run, ensure_ascii=False, sort_keys=True) + "\n")

    def append_heartbeat(self, heartbeat: dict[str, Any]) -> None:
        with self.heartbeats_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(heartbeat, ensure_ascii=False, sort_keys=True) + "\n")

    def append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        row = {"event_type": event_type, "created_at": _utc_now(), **payload}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def read_runs(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.runs_path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _job_payload(row: dict[str, Any]) -> dict[str, Any]:
    defaults = ComputeJobSpec(job_id="", job_kind="", command=[]).to_dict()
    defaults.update(row)
    return defaults


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
