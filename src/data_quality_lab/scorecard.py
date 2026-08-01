"""Build data quality scorecards and gates."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from artifact_schema.writer import utc_now

from .models import DataQualityFreezeGate, DataQualityIssue, DataQualityScorecard, DatasetQualitySummary
from .rule_registry import CORE_REQUIRED_DATASETS, EXPANDED_DATASETS, build_rule_registry
from .rules import date_range, ts_code_count


def build_dataset_summaries(datasets: dict[str, list[dict[str, Any]]], issues: list[DataQualityIssue]) -> list[DatasetQualitySummary]:
    by_dataset: dict[str, list[DataQualityIssue]] = defaultdict(list)
    for issue in issues:
        by_dataset[issue.dataset].append(issue)
    rule_counts = Counter(rule.dataset for rule in build_rule_registry())
    summaries: list[DatasetQualitySummary] = []
    for dataset in sorted(set(datasets) | set(by_dataset)):
        records = datasets.get(dataset, [])
        dataset_issues = by_dataset.get(dataset, [])
        severity = Counter(issue.severity for issue in dataset_issues)
        status = "ok"
        if severity.get("blocker", 0) or severity.get("error", 0):
            status = "error"
        elif severity.get("warning", 0):
            status = "warning"
        first_date, last_date = date_range(records)
        summaries.append(
            DatasetQualitySummary(
                dataset=dataset,
                record_count=len(records),
                rule_count=rule_counts.get(dataset, 0),
                issue_count=len(dataset_issues),
                blocker_count=severity.get("blocker", 0),
                error_count=severity.get("error", 0),
                warning_count=severity.get("warning", 0),
                info_count=severity.get("info", 0),
                status=status,
                first_date=first_date,
                last_date=last_date,
                ts_code_count=ts_code_count(records),
                sample_issue_ids=[issue.issue_id for issue in dataset_issues[:10]],
            )
        )
    return summaries


def build_scorecard(datasets: dict[str, list[dict[str, Any]]], issues: list[DataQualityIssue], metadata: dict[str, Any] | None = None) -> DataQualityScorecard:
    severity = Counter(issue.severity for issue in issues)
    rule_distribution = Counter(issue.rule_id for issue in issues)
    status = "ok"
    if severity.get("blocker", 0) or severity.get("error", 0):
        status = "error"
    elif severity.get("warning", 0):
        status = "warning"
    return DataQualityScorecard(
        status=status,
        dataset_count=len(datasets),
        issue_count=len(issues),
        blocker_count=severity.get("blocker", 0),
        error_count=severity.get("error", 0),
        warning_count=severity.get("warning", 0),
        info_count=severity.get("info", 0),
        dataset_summaries=build_dataset_summaries(datasets, issues),
        severity_distribution=dict(severity),
        rule_distribution=dict(rule_distribution),
        created_at=utc_now(),
        metadata=metadata or {},
    )


def build_freeze_gate(scorecard: DataQualityScorecard, issues: list[DataQualityIssue]) -> DataQualityFreezeGate:
    core_count = 0
    expanded_count = 0
    reasons: list[str] = []
    for issue in issues:
        if issue.severity not in {"blocker", "error"}:
            continue
        if issue.dataset in CORE_REQUIRED_DATASETS or issue.dataset == "cross_dataset":
            core_count += 1
            if len(reasons) < 10:
                reasons.append(f"{issue.dataset}: {issue.message}")
        elif issue.dataset in EXPANDED_DATASETS:
            expanded_count += 1
    can_core = core_count == 0
    can_expanded = can_core and expanded_count == 0
    status = "blocked" if core_count else ("warning" if expanded_count or scorecard.warning_count else "ok")
    action = "semantic QA passed"
    if core_count:
        action = "repair core semantic blockers before freeze/matrix/core alpha"
    elif expanded_count:
        action = "core flow may continue, but expanded alpha should wait for optional dataset repair or exclusion"
    elif scorecard.warning_count:
        action = "review warnings before production freeze"
    return DataQualityFreezeGate(
        status=status,
        can_create_freeze=can_core,
        can_build_matrix=can_core,
        can_run_core_alpha=can_core,
        can_run_expanded_alpha=can_expanded,
        blocker_count=scorecard.blocker_count + scorecard.error_count,
        core_blocker_count=core_count,
        expanded_blocker_count=expanded_count,
        recommended_next_action=action,
        reasons=reasons,
        created_at=utc_now(),
        metadata={"scorecard_status": scorecard.status},
    )
