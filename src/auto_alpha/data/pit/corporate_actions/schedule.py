"""Corporate action event scheduling helpers."""

from __future__ import annotations

from typing import Sequence

from .models import CorporateActionEvent


def build_action_schedule(
    events: Sequence[CorporateActionEvent],
    start_date: str,
    end_date: str,
    include_proposals: bool = False,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for event in events:
        if event.action_type == "proposal_only" and not include_proposals:
            continue
        schedule_date = event.effective_date or event.ann_date
        if schedule_date is None or not (start_date <= schedule_date <= end_date):
            continue
        rows.append(
            {
                "action_id": event.action_id,
                "ts_code": event.ts_code,
                "action_type": event.action_type,
                "status": event.status,
                "availability_date": event.availability_date,
                "record_date": event.record_date,
                "ex_date": event.ex_date,
                "pay_date": event.pay_date,
                "div_listdate": event.div_listdate,
                "cash_div_per_share": event.cash_div_per_share,
                "stock_distribution_ratio": event.stock_distribution_ratio,
            }
        )
    return sorted(rows, key=lambda row: (str(row.get("ex_date") or ""), str(row.get("ts_code") or "")))


def eligible_events_for_account(
    events: Sequence[CorporateActionEvent],
    trade_date: str,
    mode: str = "pay_date",
) -> list[CorporateActionEvent]:
    selected: list[CorporateActionEvent] = []
    for event in events:
        if event.action_type == "proposal_only":
            continue
        date_value = _event_date(event, mode)
        if date_value == trade_date:
            selected.append(event)
    return selected


def filter_events_available_as_of(
    events: Sequence[CorporateActionEvent],
    as_of_date: str | None,
    use_availability_date: bool = True,
) -> list[CorporateActionEvent]:
    if not as_of_date or not use_availability_date:
        return list(events)
    return [
        event
        for event in events
        if event.availability_date is None or event.availability_date <= as_of_date
    ]


def _event_date(event: CorporateActionEvent, mode: str) -> str | None:
    if mode == "pay_date":
        return event.pay_date
    if mode == "div_listdate":
        return event.div_listdate
    if mode == "record_date":
        return event.record_date
    return event.ex_date
