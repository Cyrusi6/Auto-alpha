"""Corporate-action-specific leakage checks."""

from __future__ import annotations

from pathlib import Path

from corporate_actions.normalizer import normalize_corporate_action_records
from corporate_actions.report import read_jsonl

from .models import CorporateActionLeakageResult, LeakageIssue


def audit_corporate_actions(data_dir: str | Path, as_of_date: str | None = None) -> CorporateActionLeakageResult:
    records = read_jsonl(Path(data_dir) / "corporate_actions" / "records.jsonl")
    events = normalize_corporate_action_records(records)
    future = 0
    unavailable = 0
    issues: list[LeakageIssue] = []
    for event in events:
        if as_of_date and event.availability_date and event.availability_date > as_of_date:
            future += 1
            unavailable += 1
            issues.append(
                LeakageIssue(
                    "blocker",
                    "future_corporate_action_unavailable",
                    "corporate action availability_date is after as_of_date",
                    "corporate_actions",
                    event.action_id,
                    {"availability_date": event.availability_date, "as_of_date": as_of_date},
                )
            )
    return CorporateActionLeakageResult(
        checked_events=len(events),
        corporate_action_future_event_count=future,
        unavailable_action_used_count=unavailable,
        adjustment_reconciliation_warning_count=0,
        total_return_mismatch_count=0,
        issues=issues,
    )
