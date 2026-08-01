"""Models for safe backfill repair runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class RepairJobStatus:
    pending = "pending"
    dry_run = "dry_run"
    running = "running"
    success = "success"
    failed = "failed"
    skipped = "skipped"
    resumed = "resumed"
    blocked = "blocked"


@dataclass(frozen=True)
class BackfillRepairJob:
    repair_job_id: str
    dataset: str
    reason: str
    command: str
    source_job_id: str | None = None
    status: str = RepairJobStatus.pending
    records: int = 0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillRepairBatchPlan:
    repair_batch_id: str
    generated_at: str
    mode: str
    data_dir: str
    run_dir: str | None
    staging_dir: str | None
    jobs: list[BackfillRepairJob]
    warnings: list[str] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repair_batch_id": self.repair_batch_id,
            "generated_at": self.generated_at,
            "mode": self.mode,
            "data_dir": self.data_dir,
            "run_dir": self.run_dir,
            "staging_dir": self.staging_dir,
            "jobs": [job.to_dict() for job in self.jobs],
            "warnings": list(self.warnings),
            "blocked_reasons": list(self.blocked_reasons),
            "summary": {
                "repair_job_count": len(self.jobs),
                "blocked_job_count": len(self.blocked_reasons),
                "datasets": sorted({job.dataset for job in self.jobs}),
            },
        }


@dataclass(frozen=True)
class BackfillRepairRunState:
    repair_batch_id: str
    updated_at: str
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillRepairRunReport:
    repair_run_id: str
    created_at: str
    mode: str
    status: str
    plan: BackfillRepairBatchPlan
    job_results: list[BackfillRepairJob]
    summary: dict[str, Any]
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repair_run_id": self.repair_run_id,
            "created_at": self.created_at,
            "mode": self.mode,
            "status": self.status,
            "plan": self.plan.to_dict(),
            "job_results": [job.to_dict() for job in self.job_results],
            "summary": dict(self.summary),
            "paths": dict(self.paths),
        }
