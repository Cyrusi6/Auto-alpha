"""Production phase helper functions."""

from __future__ import annotations

from .models import ProductionPhase, ProductionPhaseStatus

TERMINAL_PHASE_STATUSES = {
    ProductionPhaseStatus.success,
    ProductionPhaseStatus.warning,
    ProductionPhaseStatus.blocked,
    ProductionPhaseStatus.failed,
    ProductionPhaseStatus.skipped,
}

WAITING_PHASE_STATUSES = {
    ProductionPhaseStatus.waiting_approval,
}

APPROVAL_PHASES = {
    ProductionPhase.create_order_approval,
    ProductionPhase.wait_for_approval,
    ProductionPhase.execute_approved,
}


def is_terminal_phase_status(status: str) -> bool:
    return status in TERMINAL_PHASE_STATUSES


def is_waiting_phase_status(status: str) -> bool:
    return status in WAITING_PHASE_STATUSES


def phase_requires_approval(phase: str) -> bool:
    return phase in APPROVAL_PHASES


def default_phase_statuses(phases: list[str]) -> dict[str, str]:
    return {phase: ProductionPhaseStatus.pending for phase in phases}
