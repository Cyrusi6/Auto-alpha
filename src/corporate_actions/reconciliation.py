"""Reconcile adjustment factors and corporate action events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from .models import AdjustmentFactorReconciliationIssue, CorporateActionEvent, TotalReturnSeriesRecord


def reconcile_adjustment_factors_with_actions(
    data_dir: str | Path,
    events: Sequence[CorporateActionEvent],
    total_return_records: Sequence[TotalReturnSeriesRecord] | None = None,
    tolerance: float = 0.05,
) -> dict[str, object]:
    data_path = Path(data_dir)
    adjustment = _read_dataset(data_path, "adjustment_factors")
    issues: list[AdjustmentFactorReconciliationIssue] = []
    adj_by_stock: dict[str, list[tuple[str, float]]] = {}
    for row in adjustment:
        adj_by_stock.setdefault(str(row.get("ts_code")), []).append((str(row.get("trade_date")), float(row.get("adj_factor") or 1.0)))
    action_dates = {(event.ts_code, event.effective_date) for event in events if event.effective_date and event.action_type != "proposal_only"}
    for ts_code, ex_date in sorted(action_dates):
        series = sorted(adj_by_stock.get(ts_code, []))
        before = [value for date, value in series if date < str(ex_date)]
        after = [value for date, value in series if date >= str(ex_date)]
        if before and after and abs(after[0] - before[-1]) <= tolerance:
            issues.append(
                AdjustmentFactorReconciliationIssue(
                    severity="warning",
                    code="action_without_adjustment_change",
                    message="corporate action exists but adjustment factor barely changed",
                    ts_code=ts_code,
                    trade_date=str(ex_date),
                    metadata={"before": before[-1], "after": after[0]},
                )
            )
    for ts_code, series in adj_by_stock.items():
        ordered = sorted(series)
        for (prev_date, prev_value), (trade_date, value) in zip(ordered, ordered[1:]):
            if abs(value - prev_value) > tolerance and (ts_code, trade_date) not in action_dates:
                issues.append(
                    AdjustmentFactorReconciliationIssue(
                        severity="warning",
                        code="adjustment_change_without_action",
                        message="adjustment factor changed without same-day corporate action event",
                        ts_code=ts_code,
                        trade_date=trade_date,
                        metadata={"previous_date": prev_date, "before": prev_value, "after": value},
                    )
                )
    return {
        "tolerance": float(tolerance),
        "issue_count": len(issues),
        "warning_count": sum(issue.severity == "warning" for issue in issues),
        "error_count": sum(issue.severity == "error" for issue in issues),
        "issues": [issue.to_dict() for issue in issues],
    }


def _read_dataset(data_dir: Path, dataset: str) -> list[dict[str, object]]:
    path = data_dir / dataset / "records.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
