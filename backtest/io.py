"""I/O helpers for portfolio simulation."""

from __future__ import annotations

from typing import Any

import torch

from factor_store import FactorValueRecord, LocalFactorStore


def select_factor_id(store: LocalFactorStore, factor_id: str | None = None) -> str:
    if factor_id:
        return factor_id
    factors = store.load_factors()
    if not factors:
        raise ValueError("factor store is empty; register a factor before running a portfolio simulation")
    return factors[-1].factor_id


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
