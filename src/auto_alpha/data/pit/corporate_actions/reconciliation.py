"""Reconcile adjustment factors and corporate action events."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .models import AdjustmentFactorReconciliationIssue, CorporateActionEvent, TotalReturnSeriesRecord
from auto_alpha.platform.artifacts.storage import canonical_hash


_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_FACTOR_QUANTUM = Decimal("0.000000000001")
_CAUSAL_FACTOR_FORMULA_ID = (
    "a_share_cash_stock_theoretical_ex_price_causal_v1"
)


def derive_causal_adjustment_factor_vintages(
    daily_bars: Sequence[Mapping[str, Any]],
    event_versions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive a non-revising factor using only PIT-eligible event versions.

    The series is anchored at one on the first in-scope trading day.  On an
    effective ex-date its multiplier is ``pre_close * (1 + stock_ratio) /
    (pre_close - cash_per_share)``.  Future event versions cannot rewrite an
    earlier factor row; invalid or late-known in-scope events become explicit
    blockers instead of being silently applied.
    """

    blockers: list[dict[str, Any]] = []
    bars_by_stock: dict[str, dict[str, Decimal]] = {}
    for source_index, row in enumerate(daily_bars):
        ts_code = str(row.get("ts_code") or "")
        trade_date = _exact_date(row.get("trade_date"))
        pre_close = _positive_decimal(row.get("pre_close"))
        if not ts_code or trade_date is None or pre_close is None:
            blockers.append(
                {
                    "code": "causal_adjustment_daily_bar_invalid",
                    "source_index": source_index,
                    "ts_code": ts_code or None,
                    "trade_date": trade_date,
                }
            )
            continue
        stock_rows = bars_by_stock.setdefault(ts_code, {})
        if trade_date in stock_rows:
            blockers.append(
                {
                    "code": "causal_adjustment_daily_bar_duplicate",
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                }
            )
            continue
        stock_rows[trade_date] = pre_close

    events_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    versions_by_event: dict[tuple[str, str], list[dict[str, Any]]] = {}
    seen_versions: set[str] = set()
    for source_index, row in enumerate(event_versions):
        ts_code = str(row.get("ts_code") or "")
        event_id = str(row.get("event_id") or "")
        effective_at = _exact_date(row.get("effective_at"))
        known_at = _exact_date(row.get("known_at"))
        event_version_id = str(row.get("event_version_id") or "")
        source_hash = str(row.get("source_document_sha256") or "")
        stock_dates = bars_by_stock.get(ts_code)
        if not stock_dates:
            continue
        first_date, last_date = min(stock_dates), max(stock_dates)
        if effective_at is not None and not (
            first_date <= effective_at <= last_date
        ):
            continue
        identity_valid = bool(
            event_id
            and event_version_id
            and event_version_id not in seen_versions
            and effective_at is not None
            and known_at is not None
            and _HEX_64.fullmatch(source_hash) is not None
            and row.get("pit_evidence_eligible") is True
        )
        if not identity_valid:
            blockers.append(
                {
                    "code": "corporate_action_event_version_invalid",
                    "source_index": source_index,
                    "event_id": event_id or None,
                    "event_version_id": event_version_id or None,
                    "ts_code": ts_code or None,
                    "effective_at": effective_at,
                }
            )
            continue
        seen_versions.add(event_version_id)
        cash = _nonnegative_decimal(row.get("cash_div_per_share"))
        stock = _nonnegative_decimal(row.get("stock_distribution_ratio"))
        if cash is None or stock is None or (cash == 0 and stock == 0):
            blockers.append(
                {
                    "code": "corporate_action_economic_terms_invalid",
                    "event_version_id": event_version_id,
                    "ts_code": ts_code,
                    "effective_at": effective_at,
                }
            )
            continue
        versions_by_event.setdefault((ts_code, event_id), []).append(
            {
                "event_id": event_id,
                "event_version_id": event_version_id,
                "known_at": known_at,
                "effective_at": effective_at,
                "cash": cash,
                "stock": stock,
                "source_document_sha256": source_hash,
            }
        )

    for (ts_code, event_id), versions in sorted(versions_by_event.items()):
        stock_dates = bars_by_stock[ts_code]
        ordered = sorted(
            versions,
            key=lambda row: (
                row["known_at"],
                row["event_version_id"],
            ),
        )
        known_dates = [str(row["known_at"]) for row in ordered]
        if len(known_dates) != len(set(known_dates)):
            blockers.append(
                {
                    "code": "corporate_action_revision_order_ambiguous",
                    "event_id": event_id,
                    "ts_code": ts_code,
                }
            )
            continue
        selected: dict[str, Any] | None = None
        for effective_at in sorted(
            {str(row["effective_at"]) for row in ordered}
        ):
            known_before_effective = [
                row for row in ordered if row["known_at"] < effective_at
            ]
            if not known_before_effective:
                continue
            latest = known_before_effective[-1]
            if latest["effective_at"] == effective_at:
                selected = latest
                break
        if selected is None:
            due_versions = [
                row
                for row in ordered
                if min(stock_dates) <= row["effective_at"] <= max(stock_dates)
            ]
            if due_versions and all(
                row["known_at"] >= row["effective_at"]
                for row in due_versions
            ):
                first_due = min(
                    due_versions,
                    key=lambda row: (
                        row["effective_at"],
                        row["event_version_id"],
                    ),
                )
                blockers.append(
                    {
                        "code": "corporate_action_known_after_effective",
                        "event_id": event_id,
                        "event_version_id": first_due["event_version_id"],
                        "ts_code": ts_code,
                        "known_at": first_due["known_at"],
                        "effective_at": first_due["effective_at"],
                        "source_document_sha256": first_due[
                            "source_document_sha256"
                        ],
                    }
                )
            continue
        effective_at = str(selected["effective_at"])
        if effective_at not in stock_dates:
            blockers.append(
                {
                    "code": "corporate_action_effective_date_not_trading_day",
                    "event_id": event_id,
                    "event_version_id": selected["event_version_id"],
                    "ts_code": ts_code,
                    "effective_at": effective_at,
                }
            )
            continue
        events_by_key.setdefault((ts_code, effective_at), []).append(selected)

    factor_rows: list[dict[str, Any]] = []
    for ts_code, stock_dates in sorted(bars_by_stock.items()):
        factor = Decimal("1")
        for trade_date, pre_close in sorted(stock_dates.items()):
            applicable = sorted(
                events_by_key.get((ts_code, trade_date), ()),
                key=lambda row: row["event_version_id"],
            )
            event_ids: list[str] = []
            event_version_ids: list[str] = []
            if applicable:
                cash = sum(
                    (row["cash"] for row in applicable),
                    start=Decimal("0"),
                )
                stock = sum(
                    (row["stock"] for row in applicable),
                    start=Decimal("0"),
                )
                denominator = pre_close - cash
                if denominator <= 0:
                    blockers.append(
                        {
                            "code": "corporate_action_theoretical_price_invalid",
                            "ts_code": ts_code,
                            "effective_at": trade_date,
                            "pre_close": str(pre_close),
                            "cash_div_per_share": str(cash),
                        }
                    )
                else:
                    factor *= pre_close * (Decimal("1") + stock) / denominator
                    event_ids = [str(row["event_id"]) for row in applicable]
                    event_version_ids = [
                        str(row["event_version_id"]) for row in applicable
                    ]
            factor_rows.append(
                {
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "causal_adj_factor": str(
                        factor.quantize(
                            _FACTOR_QUANTUM,
                            rounding=ROUND_HALF_EVEN,
                        )
                    ),
                    "event_ids": event_ids,
                    "event_version_ids": event_version_ids,
                    "knowledge_cutoff": trade_date,
                    "formula_id": _CAUSAL_FACTOR_FORMULA_ID,
                }
            )

    blockers = sorted(
        blockers,
        key=lambda row: (
            str(row.get("ts_code") or ""),
            str(row.get("effective_at") or row.get("trade_date") or ""),
            str(row.get("code") or ""),
            str(row.get("event_version_id") or ""),
            int(row.get("source_index") or 0),
        ),
    )
    semantic = {
        "schema_version": "causal_adjustment_factor_vintage_v1",
        "formula_id": _CAUSAL_FACTOR_FORMULA_ID,
        "daily_bars_input_count": len(daily_bars),
        "daily_bars_input_root": canonical_hash(
            [dict(row) for row in daily_bars]
        ),
        "event_versions_input_count": len(event_versions),
        "event_versions_input_root": canonical_hash(
            [dict(row) for row in event_versions]
        ),
        "row_count": len(factor_rows),
        "rows_root": canonical_hash(factor_rows),
        "blockers": blockers,
        "derivation_complete": not blockers,
        "data_admission_eligible": False,
        "downstream_eligible": False,
        "event_coverage_admission_verdict_required": True,
        "independent_admission_verdict_required": True,
    }
    return semantic | {
        "rows": factor_rows,
        "content_hash": canonical_hash(semantic),
    }


def _exact_date(value: Any) -> str | None:
    text = str(value or "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def _positive_decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _nonnegative_decimal(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


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
