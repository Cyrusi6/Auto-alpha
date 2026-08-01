"""ETA estimation for backfill observer reports."""

from __future__ import annotations

from .models import BackfillDatasetProgress, BackfillEtaEstimate


def estimate_eta(progress: list[BackfillDatasetProgress], rate_limit_per_minute: int | None = None) -> BackfillEtaEstimate:
    remaining = sum(item.pending_jobs + item.failed_jobs for item in progress)
    completed = sum(item.success_jobs + item.resumed_jobs for item in progress)
    theoretical = float(rate_limit_per_minute or 0)
    observed = theoretical if theoretical > 0 else 0.0
    assumptions = [
        "ETA is a point-in-time estimate from current remaining jobs.",
        "When recent event timestamps are unavailable, the configured rate limit is used as the request-bound rate.",
    ]
    if observed <= 0:
        minutes = None
        confidence = "low"
    else:
        minutes = float(remaining) / observed
        confidence = "medium" if completed else "low"
    return BackfillEtaEstimate(
        observed_jobs_per_minute=observed,
        observed_requests_per_minute=observed,
        remaining_jobs=int(remaining),
        estimated_remaining_minutes=minutes,
        confidence=confidence,
        assumptions=assumptions,
    )
