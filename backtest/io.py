"""I/O helpers for portfolio simulation."""

from __future__ import annotations

from typing import Any

import torch

from factor_store import FactorValueRecord, LocalFactorStore


def select_factor_id(
    store: LocalFactorStore,
    factor_id: str | None = None,
    latest_approved: bool = False,
    factor_type: str = "any",
) -> str:
    if factor_id:
        return factor_id
    factors = store.load_factors()
    if factor_type not in {"single", "composite", "any"}:
        raise ValueError(f"unsupported factor_type: {factor_type}")
    if factor_type != "any":
        factors = [record for record in factors if _record_factor_type(record) == factor_type]
    if latest_approved:
        approved = [record for record in factors if record.status == "approved"]
        if approved:
            return approved[-1].factor_id
    if not factors:
        raise ValueError("factor store is empty; register a factor before running a portfolio simulation")
    return factors[-1].factor_id


def describe_factor(store: LocalFactorStore, factor_id: str) -> dict[str, Any]:
    for record in store.load_factors():
        if record.factor_id == factor_id:
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            component_ids = (
                record.parent_factor_ids
                or metadata.get("component_factor_ids")
                or []
            )
            return {
                "factor_id": record.factor_id,
                "factor_type": _record_factor_type(record),
                "component_factor_ids": list(component_ids) if isinstance(component_ids, list) else [],
                "status": record.status,
                "batch_id": record.batch_id,
            }
    return {
        "factor_id": factor_id,
        "factor_type": "unknown",
        "component_factor_ids": [],
        "status": "",
        "batch_id": None,
    }


def factor_values_to_matrix(
    records: list[FactorValueRecord],
    ts_codes: list[str],
    trade_dates: list[str],
    device: Any = None,
) -> torch.Tensor:
    code_index = {ts_code: idx for idx, ts_code in enumerate(ts_codes)}
    date_index = {trade_date: idx for idx, trade_date in enumerate(trade_dates)}
    matrix = torch.zeros((len(ts_codes), len(trade_dates)), dtype=torch.float32, device=device)
    for record in records:
        if record.ts_code not in code_index or record.trade_date not in date_index:
            continue
        matrix[code_index[record.ts_code], date_index[record.trade_date]] = (
            0.0 if record.value is None else float(record.value)
        )
    return matrix


def _record_factor_type(record) -> str:
    return record.factor_type or "single"
