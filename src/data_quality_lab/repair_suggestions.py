"""Repair suggestions for data quality issues.

Suggestions are intentionally non-mutating. They describe likely next actions
and command hints, but never execute repairs.
"""

from __future__ import annotations

import hashlib

from .models import DataQualityIssue, DataQualityRepairSuggestion


def build_repair_suggestions(issues: list[DataQualityIssue]) -> list[DataQualityRepairSuggestion]:
    suggestions: list[DataQualityRepairSuggestion] = []
    for issue in issues:
        action = issue.repair_action or _action_for_issue(issue)
        suggestions.append(
            DataQualityRepairSuggestion(
                suggestion_id=_suggestion_id(issue.issue_id, action),
                dataset=issue.dataset,
                issue_id=issue.issue_id,
                rule_id=issue.rule_id,
                severity=issue.severity,
                action=action,
                command_hint=_command_hint(issue.dataset, action),
                automatic=False,
                reason=issue.message,
                metadata={"field": issue.field, "key": issue.key},
            )
        )
    return suggestions


def _action_for_issue(issue: DataQualityIssue) -> str:
    if "duplicate" in issue.rule_id:
        return "compact_dedup"
    if "pit" in issue.rule_id or "ann_date" in issue.message:
        return "require_pit_review"
    if issue.severity in {"blocker", "error"}:
        return "block_freeze_until_repaired"
    return "allow_with_warning"


def _command_hint(dataset: str, action: str) -> str:
    if action == "compact_dedup":
        return f"uv run python -m data_pipeline.run_pipeline --data-dir <stable_data_dir> --compact --datasets {dataset} --pretty"
    if action == "require_pit_review":
        return f"uv run python -m point_in_time.run_pit validate --data-dir <stable_data_dir> --datasets {dataset} --pretty"
    if action == "exclude_dataset_from_feature_family":
        return f"uv run python -m feature_promotion.run_promotion init-policy --denied-features-from-dataset {dataset} --pretty"
    if action == "inspect_empty_response":
        return f"uv run python -m data_source_validation.run_smoke --provider tushare --datasets {dataset} --allow-network --max-requests 5 --pretty"
    return f"uv run python -m backfill_repair.run_repair plan --data-dir <stable_data_dir> --datasets {dataset} --pretty"


def _suggestion_id(issue_id: str, action: str) -> str:
    digest = hashlib.sha256(f"{issue_id}|{action}".encode("utf-8")).hexdigest()[:16]
    return f"dq_repair_{digest}"
