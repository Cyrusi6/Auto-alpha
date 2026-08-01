"""Normalize provider dividend records into deterministic corporate action events."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from .models import CorporateActionEvent, CorporateActionType


def normalize_corporate_action_records(
    records: Sequence[dict[str, Any]],
    apply_statuses: Sequence[str] = ("实施",),
    cash_field: str = "cash_div",
) -> list[CorporateActionEvent]:
    statuses = tuple(str(status) for status in apply_statuses)
    events: list[CorporateActionEvent] = []
    for record in records:
        ts_code = str(record.get("ts_code") or "")
        if not ts_code:
            continue
        status = str(record.get("div_proc") or record.get("raw_status") or "")
        implemented = any(status_value and status_value in status for status_value in statuses)
        cash_div = _float(record.get(cash_field))
        cash_tax = _float(record.get("cash_div_tax"))
        stock_bonus = _float(record.get("stk_div") if record.get("stk_div") not in {None, ""} else record.get("stk_bo_rate"))
        stock_transfer = _float(record.get("stk_co_rate"))
        stock_ratio = max(0.0, stock_bonus) + max(0.0, stock_transfer)
        action_type = _action_type(implemented, cash_div, stock_bonus, stock_transfer)
        ann_date = _date(record.get("ann_date"))
        imp_ann_date = _date(record.get("imp_ann_date"))
        ex_date = _date(record.get("ex_date"))
        event = CorporateActionEvent(
            action_id=_action_id(record),
            ts_code=ts_code,
            action_type=action_type.value,
            status=status or "unknown",
            end_date=_date(record.get("end_date")),
            ann_date=ann_date,
            imp_ann_date=imp_ann_date,
            record_date=_date(record.get("record_date")),
            ex_date=ex_date,
            pay_date=_date(record.get("pay_date")),
            div_listdate=_date(record.get("div_listdate")),
            cash_div_per_share=max(0.0, cash_div),
            cash_div_tax_per_share=max(0.0, cash_tax),
            stock_bonus_ratio=max(0.0, stock_bonus),
            stock_transfer_ratio=max(0.0, stock_transfer),
            stock_distribution_ratio=stock_ratio,
            availability_date=imp_ann_date or ann_date,
            effective_date=ex_date,
            source_record=dict(record),
            unit_assumption="per_share_from_provider",
            metadata={
                "implemented": implemented,
                "cash_field": cash_field,
                "base_date": _date(record.get("base_date")),
                "base_share": _float(record.get("base_share")),
                "source": record.get("source"),
            },
        )
        events.append(event)
    return sorted(events, key=lambda event: (event.ts_code, event.effective_date or "", event.action_id))


def _action_id(record: dict[str, Any]) -> str:
    payload = {
        "ts_code": record.get("ts_code"),
        "ann_date": record.get("ann_date"),
        "end_date": record.get("end_date"),
        "ex_date": record.get("ex_date"),
        "pay_date": record.get("pay_date"),
        "div_proc": record.get("div_proc") or record.get("raw_status"),
        "cash_div": record.get("cash_div"),
        "cash_div_tax": record.get("cash_div_tax"),
        "stk_div": record.get("stk_div"),
        "stk_bo_rate": record.get("stk_bo_rate"),
        "stk_co_rate": record.get("stk_co_rate"),
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"ca_{digest[:16]}"


def _action_type(
    implemented: bool,
    cash_div: float,
    stock_bonus: float,
    stock_transfer: float,
) -> CorporateActionType:
    if not implemented:
        return CorporateActionType.PROPOSAL_ONLY
    has_cash = cash_div > 0
    has_stock_bonus = stock_bonus > 0
    has_stock_transfer = stock_transfer > 0
    if has_cash and (has_stock_bonus or has_stock_transfer):
        return CorporateActionType.COMBINED_DISTRIBUTION
    if has_cash:
        return CorporateActionType.CASH_DIVIDEND
    if has_stock_bonus:
        return CorporateActionType.STOCK_BONUS
    if has_stock_transfer:
        return CorporateActionType.STOCK_TRANSFER
    return CorporateActionType.UNKNOWN


def _float(value: Any) -> float:
    try:
        if value in {None, ""}:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _date(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text if len(text) == 8 and text.isdigit() else None
