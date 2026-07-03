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
