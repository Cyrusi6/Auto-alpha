"""Security lifecycle and active mask builders."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import ActiveSecurityMask, SecurityLifecycleRecord


def build_security_lifecycle(securities: Iterable[dict]) -> list[SecurityLifecycleRecord]:
    records: list[SecurityLifecycleRecord] = []
    for item in securities:
        ts_code = str(item.get("ts_code") or "")
        list_date = str(item.get("list_date") or "")
        if not ts_code or not list_date:
            continue
        name = str(item.get("raw_name") or item.get("name") or "")
        status = str(item.get("list_status") or "unknown").upper()
        records.append(
            SecurityLifecycleRecord(
                ts_code=ts_code,
                symbol=str(item.get("symbol") or ts_code.split(".")[0]),
                name=name,
                list_date=list_date,
                delist_date=str(item.get("delist_date")) if item.get("delist_date") not in {None, ""} else None,
                list_status=status,
                is_st=bool(item.get("is_st", False)),
                exchange=str(item.get("exchange") or "") or None,
                board=str(item.get("board") or "") or None,
                industry=str(item.get("industry") or "") or None,
                area=str(item.get("area") or "") or None,
            )
        )
    return sorted(records, key=lambda record: record.ts_code)


def build_active_security_mask(
    lifecycle: Iterable[SecurityLifecycleRecord],
    trade_dates: Iterable[str],
    min_listing_days: int = 0,
    exclude_st: bool = False,
    include_paused: bool = False,
    include_delisted_history: bool = True,
    board_filters: set[str] | None = None,
    exchange_filters: set[str] | None = None,
) -> list[ActiveSecurityMask]:
    rows: list[ActiveSecurityMask] = []
    for security in lifecycle:
        if board_filters and (security.board or "") not in board_filters:
            continue
        if exchange_filters and (security.exchange or "") not in exchange_filters:
            continue
        for trade_date in sorted(trade_dates):
            active, reason, age = _active_reason(
                security,
                trade_date,
                min_listing_days=min_listing_days,
                exclude_st=exclude_st,
                include_paused=include_paused,
                include_delisted_history=include_delisted_history,
            )
            rows.append(
                ActiveSecurityMask(
                    ts_code=security.ts_code,
                    trade_date=str(trade_date),
                    is_active=active,
                    reason=reason,
                    listing_age_days=age,
                    list_status=security.list_status,
                    is_st=security.is_st,
                )
            )
    return rows


def load_security_lifecycle(path: str | Path) -> list[SecurityLifecycleRecord]:
    target = Path(path)
    if not target.exists():
        return []
    return [SecurityLifecycleRecord(**json.loads(line)) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_active_security_mask(path: str | Path) -> list[ActiveSecurityMask]:
    target = Path(path)
    if not target.exists():
        return []
    return [ActiveSecurityMask(**json.loads(line)) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def _active_reason(
    security: SecurityLifecycleRecord,
    trade_date: str,
    min_listing_days: int,
    exclude_st: bool,
    include_paused: bool,
    include_delisted_history: bool,
) -> tuple[bool, str, int]:
    age = _date_diff_days(security.list_date, trade_date)
    if age < 0:
        return False, "not_listed_yet", age
    if age < min_listing_days:
        return False, "listing_age_below_minimum", age
    if exclude_st and security.is_st:
        return False, "st_excluded", age
    if security.list_status == "P" and not include_paused:
        return False, "paused", age
    if security.delist_date and trade_date >= security.delist_date:
        return False, "delisted", age
    if security.list_status == "D" and not include_delisted_history and security.delist_date:
        return False, "delisted_status", age
    return True, "active", age


def _date_diff_days(left: str, right: str) -> int:
    try:
        return (datetime.strptime(right, "%Y%m%d") - datetime.strptime(left, "%Y%m%d")).days
    except ValueError:
        return -999999
