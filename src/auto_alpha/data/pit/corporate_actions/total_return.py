"""Build simple corporate-action-aware total-return series."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from .models import CorporateActionEvent, TotalReturnSeriesRecord


def build_total_return_series(
    data_dir: str | Path,
    events: Sequence[CorporateActionEvent],
    mode: str = "cash_reinvested",
) -> list[TotalReturnSeriesRecord]:
    bars = _read_dataset(Path(data_dir), "daily_bars")
    adj = {(row.get("ts_code"), row.get("trade_date")): float(row.get("adj_factor") or 1.0) for row in _read_dataset(Path(data_dir), "adjustment_factors")}
    actions_by_key: dict[tuple[str, str], list[CorporateActionEvent]] = {}
    for event in events:
        if event.effective_date:
            actions_by_key.setdefault((event.ts_code, event.effective_date), []).append(event)
    rows = sorted(bars, key=lambda row: (str(row.get("ts_code")), str(row.get("trade_date"))))
    previous_price: dict[str, float] = {}
    records: list[TotalReturnSeriesRecord] = []
    for row in rows:
        ts_code = str(row.get("ts_code") or "")
        trade_date = str(row.get("trade_date") or "")
        close = float(row.get("close") or 0.0)
        adjusted_close = close * adj.get((ts_code, trade_date), float(row.get("adj_factor") or 1.0))
        actions = actions_by_key.get((ts_code, trade_date), [])
        cash = sum(event.cash_div_per_share for event in actions if event.action_type != "proposal_only")
        stock_ratio = sum(event.stock_distribution_ratio for event in actions if event.action_type != "proposal_only")
        if mode == "price_only":
            tr_price = close
        elif mode == "cash_dividend":
            tr_price = close + cash
        else:
            tr_price = close * (1.0 + stock_ratio) + cash
        prev = previous_price.get(ts_code)
        total_return = 0.0 if prev is None or prev <= 0 else tr_price / prev - 1.0
        previous_price[ts_code] = tr_price
        records.append(
            TotalReturnSeriesRecord(
                trade_date=trade_date,
                ts_code=ts_code,
                raw_close=close,
                adjusted_close=adjusted_close,
                cash_dividend=float(cash),
                stock_distribution_ratio=float(stock_ratio),
                total_return_price=float(tr_price),
                total_return=float(total_return),
                action_flag=bool(actions),
            )
        )
    return records


def _read_dataset(data_dir: Path, dataset: str) -> list[dict[str, object]]:
    path = data_dir / dataset / "records.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
