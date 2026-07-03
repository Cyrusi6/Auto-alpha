"""Models for read-only backfill observation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BackfillObservedRun:
    run_id: str
    run_dir: str
    data_dir: str
    staging_dir: str | None
    cache_dir: str | None
    observed_at: str
    active_dataset: str | None
    active_job_id: str | None
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillDatasetProgress:
    dataset: str
    total_jobs: int
    success_jobs: int
    failed_jobs: int
    skipped_jobs: int
    pending_jobs: int
    quarantined_jobs: int
    resumed_jobs: int
    progress_ratio: float
    records: int
    size_bytes: int
    first_date: str | None
    last_date: str | None
    ts_code_count: int
    empty_response_count: int
    permission_error_count: int
    rate_limit_error_count: int
    timeout_count: int
    latest_event_at: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillEtaEstimate:
    observed_jobs_per_minute: float
    observed_requests_per_minute: float
    remaining_jobs: int
    estimated_remaining_minutes: float | None
    confidence: str
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillRepairPlan:
    repair_plan_id: str
    generated_at: str
    failed_jobs: int
    missing_jobs: int
    empty_but_expected_jobs: int
    commands: list[str]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillPostprocessStep:
    step_id: str
    description: str
    command: str
    blocked: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillPostprocessPlan:
    plan_id: str
    prerequisites: dict[str, bool]
    steps: list[BackfillPostprocessStep]
    commands: list[str]
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "prerequisites": dict(self.prerequisites),
            "steps": [step.to_dict() for step in self.steps],
            "commands": list(self.commands),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class BackfillObserverIssue:
    severity: str
    code: str
    message: str
    dataset: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillObserverReport:
    report_id: str
    observed_at: str
    observed_run: BackfillObservedRun
    datasets: list[BackfillDatasetProgress]
    eta: BackfillEtaEstimate
    repair_plan: BackfillRepairPlan
    postprocess_plan: BackfillPostprocessPlan
    issues: list[BackfillObserverIssue]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "observed_at": self.observed_at,
            "observed_run": self.observed_run.to_dict(),
            "datasets": [item.to_dict() for item in self.datasets],
            "eta": self.eta.to_dict(),
            "repair_plan": self.repair_plan.to_dict(),
            "postprocess_plan": self.postprocess_plan.to_dict(),
            "issues": [issue.to_dict() for issue in self.issues],
            "summary": dict(self.summary),
        }
