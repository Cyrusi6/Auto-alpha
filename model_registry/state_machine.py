"""Lifecycle transition rules for local model registry."""

from __future__ import annotations

from .models import ModelLifecycleAction, ModelLifecycleStatus, TERMINAL_STATUSES


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ModelLifecycleStatus.research_candidate: {
        ModelLifecycleStatus.approved,
        ModelLifecycleStatus.production_candidate,
        ModelLifecycleStatus.rejected,
        ModelLifecycleStatus.retired,
    },
    ModelLifecycleStatus.approved: {
        ModelLifecycleStatus.production_candidate,
        ModelLifecycleStatus.active,
        ModelLifecycleStatus.paused,
        ModelLifecycleStatus.rejected,
        ModelLifecycleStatus.retired,
    },
    ModelLifecycleStatus.production_candidate: {
        ModelLifecycleStatus.active,
        ModelLifecycleStatus.paused,
        ModelLifecycleStatus.quarantined,
        ModelLifecycleStatus.deprecated,
        ModelLifecycleStatus.retired,
        ModelLifecycleStatus.rejected,
    },
    ModelLifecycleStatus.active: {
        ModelLifecycleStatus.paused,
        ModelLifecycleStatus.quarantined,
        ModelLifecycleStatus.deprecated,
        ModelLifecycleStatus.retired,
    },
    ModelLifecycleStatus.paused: {
        ModelLifecycleStatus.active,
        ModelLifecycleStatus.quarantined,
        ModelLifecycleStatus.retired,
    },
    ModelLifecycleStatus.quarantined: {
        ModelLifecycleStatus.active,
        ModelLifecycleStatus.paused,
        ModelLifecycleStatus.retired,
    },
    ModelLifecycleStatus.deprecated: {
        ModelLifecycleStatus.active,
        ModelLifecycleStatus.retired,
    },
}


def validate_transition(
    from_status: str,
    to_status: str,
    action: str,
    *,
    approval_id: str | None = None,
    explicit_override: bool = False,
) -> None:
    if from_status in TERMINAL_STATUSES:
        raise ValueError(f"terminal model status cannot transition: {from_status} -> {to_status}")
    if from_status == ModelLifecycleStatus.quarantined and to_status == ModelLifecycleStatus.active and not explicit_override:
        raise ValueError("quarantined model activation requires explicit_override=True")
    if to_status == ModelLifecycleStatus.active and from_status == ModelLifecycleStatus.production_candidate:
        if not approval_id and not explicit_override:
            raise ValueError("production_candidate activation requires approval_id or explicit_override=True")
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"illegal model lifecycle transition: {from_status} -> {to_status}")
    if action == ModelLifecycleAction.resume and to_status != ModelLifecycleStatus.active:
        raise ValueError("resume action must transition to active")
