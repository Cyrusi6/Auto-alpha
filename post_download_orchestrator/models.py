"""Models for post-download orchestration plans."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PostDownloadStep:
    step_id: str
    description: str
    command: str
    required: bool = True
    blocked: bool = False
    reason: str | None = None
    status: str = "planned"
    resume_policy: str = "skip_if_success"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PostDownloadPlan:
    plan_id: str
    created_at: str
    profile_name: str | None
    readiness_status: str
    allow_incomplete: bool
    steps: list[PostDownloadStep]
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_step: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "profile_name": self.profile_name,
            "readiness_status": self.readiness_status,
            "allow_incomplete": self.allow_incomplete,
            "steps": [step.to_dict() for step in self.steps],
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "next_step": self.next_step,
        }


@dataclass(frozen=True)
class PostDownloadRunReport:
    run_id: str
    created_at: str
    mode: str
    status: str
    plan: PostDownloadPlan
    executed_steps: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "mode": self.mode,
            "status": self.status,
            "plan": self.plan.to_dict(),
            "executed_steps": list(self.executed_steps),
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class PostDownloadStepRun:
    step_id: str
    status: str
    started_at: str
    ended_at: str
    input_artifacts: list[str] = field(default_factory=list)
    output_artifacts: list[str] = field(default_factory=list)
    command: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    blocker_reason: str | None = None
    resume_policy: str = "skip_if_success"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PostDownloadState:
    run_id: str
    plan_id: str
    updated_at: str
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FreezeCandidatePackage:
    package_id: str
    created_at: str
    status: str
    data_dir: str
    run_dir: str | None
    proposed_freeze_name: str
    proposed_matrix_cache_dir: str | None
    observer_report_path: str | None = None
    raw_landing_report_path: str | None = None
    repair_report_path: str | None = None
    research_readiness_report_path: str | None = None
    dataset_progress_summary: dict[str, Any] = field(default_factory=dict)
    dataset_size_summary: dict[str, Any] = field(default_factory=dict)
    failed_quarantined_summary: dict[str, Any] = field(default_factory=dict)
    pit_safety_summary: dict[str, Any] = field(default_factory=dict)
    proposed_dataset_version_metadata: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommended_next_command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
