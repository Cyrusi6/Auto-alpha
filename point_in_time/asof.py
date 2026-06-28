"""As-of joins and feature cutoff utilities."""

from __future__ import annotations

from typing import Any, Iterable


def asof_join(
    records: Iterable[dict[str, Any]],
    entity_key: str,
    event_date_field: str,
    availability_date_field: str,
    as_of_date: str,
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        entity = str(record.get(entity_key) or "")
        available_on = str(record.get(availability_date_field) or record.get(event_date_field) or "")
        if not entity or not available_on or available_on > as_of_date:
            continue
        current = latest.get(entity)
        current_date = str(current.get(availability_date_field) or current.get(event_date_field) or "") if current else ""
        if current is None or available_on >= current_date:
            latest[entity] = dict(record)
    return latest


def compute_feature_cutoff_date(trade_date: str, mode: str, previous_trade_date: str | None = None) -> str:
    if mode == "same_day_after_close":
        return trade_date
    if mode in {"next_trade_day_open", "previous_trade_day_close"}:
        return previous_trade_date or trade_date
    raise ValueError("feature_cutoff_mode must be same_day_after_close, next_trade_day_open, or previous_trade_day_close")
