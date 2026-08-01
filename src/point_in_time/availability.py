"""Availability alignment helpers for point-in-time validation."""

from __future__ import annotations

from typing import Any, Iterable

from .asof import compute_feature_cutoff_date
from .models import PITDatasetContract


def align_by_availability(
    records: Iterable[dict[str, Any]],
    trade_dates: list[str],
    ts_codes: list[str],
    value_field: str,
    contract: PITDatasetContract,
) -> dict[tuple[str, str], Any]:
    aligned: dict[tuple[str, str], Any] = {}
    entity = contract.entity_field or "ts_code"
    availability = contract.availability_date_field or contract.date_field
    if not availability:
        return aligned
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get(entity) or ""), []).append(record)
    for ts_code in ts_codes:
        rows = sorted(grouped.get(ts_code, []), key=lambda item: str(item.get(availability) or ""))
        for trade_date in trade_dates:
            available = [row for row in rows if str(row.get(availability) or "") <= trade_date]
            if available:
                aligned[(ts_code, trade_date)] = available[-1].get(value_field)
    return aligned


def validate_no_future_availability(records: Iterable[dict[str, Any]], trade_date: str, contract: PITDatasetContract) -> list[dict[str, Any]]:
    availability = contract.availability_date_field or contract.date_field
    if not availability:
        return []
    return [record for record in records if str(record.get(availability) or "") > trade_date]


__all__ = ["align_by_availability", "compute_feature_cutoff_date", "validate_no_future_availability"]
