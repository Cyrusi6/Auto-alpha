"""Shared helpers for semantic data quality checks."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from typing import Any, Iterable

from .models import DataQualityIssue
from .rule_registry import rules_by_id


DATE_FIELDS = [
    "trade_date",
    "cal_date",
    "ann_date",
    "end_date",
    "ex_date",
    "record_date",
    "pay_date",
    "in_date",
    "out_date",
    "list_date",
    "delist_date",
    "float_date",
    "change_date",
    "disclosure_date",
    "publish_date",
]


class IssueCollector:
    def __init__(self, max_sample_issues: int = 1000):
        self.max_sample_issues = max(1, int(max_sample_issues))
        self._issues: list[DataQualityIssue] = []
        self._counts: dict[str, int] = defaultdict(int)
        self._rule_map = rules_by_id()

    @property
    def issues(self) -> list[DataQualityIssue]:
        return list(self._issues)

    def add(
        self,
        rule_id: str,
        dataset: str,
        message: str,
        *,
        severity: str | None = None,
        key: str | None = None,
        field: str | None = None,
        sample: dict[str, Any] | None = None,
        repair_action: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._counts[rule_id] += 1
        if self._counts[rule_id] > self.max_sample_issues:
            return
        rule = self._rule_map.get(rule_id)
        final_severity = severity or (rule.severity if rule else "warning")
        final_action = repair_action or (rule.suggestion_action if rule else "inspect_empty_response")
        issue_key = key or ""
        issue_id = _stable_id(rule_id, dataset, issue_key, message, self._counts[rule_id])
        self._issues.append(
            DataQualityIssue(
                issue_id=issue_id,
                rule_id=rule_id,
                dataset=dataset,
                severity=final_severity,
                message=message,
                key=key,
                field=field,
                sample=_sanitize_sample(sample or {}),
                repair_action=final_action,
                metadata=metadata or {},
            )
        )


def duplicate_key_issues(
    dataset: str,
    records: Iterable[dict[str, Any]],
    key_fields: list[str],
    collector: IssueCollector,
    rule_id: str,
) -> None:
    if not key_fields:
        return
    seen: set[tuple[str, ...]] = set()
    for record in records:
        key = tuple(str(record.get(field, "")) for field in key_fields)
        if any(value == "" for value in key):
            continue
        if key in seen:
            collector.add(rule_id, dataset, "duplicate primary key", key="|".join(key), sample=record)
        else:
            seen.add(key)


def is_valid_date(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 8 and text.isdigit()


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def date_value(record: dict[str, Any]) -> str | None:
    for field in DATE_FIELDS:
        value = record.get(field)
        if value:
            return str(value)
    return None


def date_range(records: Iterable[dict[str, Any]]) -> tuple[str | None, str | None]:
    values = sorted({str(value) for record in records for value in [date_value(record)] if value})
    return (values[0], values[-1]) if values else (None, None)


def ts_code_count(records: Iterable[dict[str, Any]]) -> int:
    return len({str(record.get("ts_code")) for record in records if record.get("ts_code")})


def record_key(record: dict[str, Any], fields: list[str]) -> str:
    return "|".join(str(record.get(field, "")) for field in fields)


def _stable_id(rule_id: str, dataset: str, key: str, message: str, index: int) -> str:
    digest = hashlib.sha256(f"{rule_id}|{dataset}|{key}|{message}|{index}".encode("utf-8")).hexdigest()[:16]
    return f"dq_{digest}"


def _sanitize_sample(sample: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in sample.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[str(key)] = value
        else:
            clean[str(key)] = str(value)
    return clean
