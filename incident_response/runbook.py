"""Runbook suggestions for local incidents."""

from __future__ import annotations

from .models import IncidentRunbookStep


def build_runbook_steps(code: str) -> list[IncidentRunbookStep]:
    base = [
        IncidentRunbookStep("inspect_artifact", "Inspect Artifact", "Open the referenced artifact and verify the reported value."),
        IncidentRunbookStep("stop_next_phase", "Stop Next Phase", "Do not advance the production run until the incident is reviewed."),
    ]
    if code in {"missing_active_model", "missing_active_optimizer_policy", "certification_blocked"}:
        base.append(IncidentRunbookStep("request_approval", "Request Approval", "Complete model or portfolio policy approval before resuming."))
    if code in {"risk_kill_switch_active", "risk_blocker"}:
        base.append(IncidentRunbookStep("risk_review", "Risk Review", "Review risk control state and keep the kill switch active until cleared."))
    if code in {"eod_reconciliation_blocker", "settlement_nav_mismatch"}:
        base.append(IncidentRunbookStep("run_reconciliation", "Run Reconciliation", "Run EOD reconciliation and resolve material breaks."))
    base.append(IncidentRunbookStep("resume_phase", "Resume Phase", "Rerun the blocked phase with resume after validation."))
    return base
