"""Build A-share research universes from local JSONL datasets."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from data_pipeline.ashare.storage import LocalAshareStorage
from data_pipeline.ashare.validators import is_valid_ts_code, is_valid_yyyymmdd

from .models import UniverseBuildConfig, UniverseBuildResult, UniverseMember


def build_universe_from_storage(
    storage: LocalAshareStorage,
    config: UniverseBuildConfig,
) -> UniverseBuildResult:
    if not is_valid_yyyymmdd(config.as_of_date):
        raise ValueError("as_of_date must be a real date in YYYYMMDD format")

    securities = storage.read_dataset("securities")
    index_codes, latest_index_trade_date = _index_member_codes(
        storage.read_dataset("index_members"),
        config.as_of_date,
        config.index_code,
    ) if config.use_index_members else (None, None)
    if config.use_index_members and config.index_code is None:
        raise ValueError("index_code is required when use_index_members=True")
    if index_codes is not None:
        securities = [record for record in securities if str(record.get("ts_code")) in index_codes]
    bars_by_code = _latest_bars_by_code(storage.read_dataset("daily_bars"), config.as_of_date)
    rejected: Counter[str] = Counter()
    members: list[UniverseMember] = []

    for security in securities:
        member = _build_member_candidate(security, bars_by_code, config, rejected)
        if member is not None:
            members.append(member)

    members.sort(key=lambda item: item.ts_code)
    output_path, summary_path = _write_universe(
        storage.data_dir,
        config,
        members,
        len(securities),
        rejected,
        source="index_members" if config.use_index_members else "securities",
        latest_index_trade_date=latest_index_trade_date,
    )
    return UniverseBuildResult(
        universe_name=config.universe_name,
        as_of_date=config.as_of_date,
        members=members,
        output_path=str(output_path),
        summary_path=str(summary_path),
        total_candidates=len(securities),
        selected=len(members),
        rejected=dict(rejected),
        source="index_members" if config.use_index_members else "securities",
        index_code=config.index_code,
        latest_index_trade_date=latest_index_trade_date,
    )


def _build_member_candidate(
    security: dict[str, Any],
    bars_by_code: dict[str, dict[str, Any]],
    config: UniverseBuildConfig,
    rejected: Counter[str],
) -> UniverseMember | None:
    ts_code = str(security.get("ts_code", ""))
    if not is_valid_ts_code(ts_code):
        rejected["invalid_ts_code"] += 1
        return None

    if bool(security.get("is_st", False)):
        rejected["st_security"] += 1
        return None

    list_date = str(security.get("list_date", ""))
    if not is_valid_yyyymmdd(list_date):
        rejected["invalid_list_date"] += 1
        return None

    delist_date = security.get("delist_date")
    if delist_date not in {None, ""} and str(delist_date) <= config.as_of_date:
        rejected["delisted"] += 1
        return None

    exchange = str(security.get("exchange") or "")
    board = None if security.get("board") in {None, ""} else str(security.get("board"))
    if config.exchanges is not None and exchange not in config.exchanges:
        rejected["exchange_filter"] += 1
        return None
    if config.boards is not None and board not in config.boards:
        rejected["board_filter"] += 1
        return None

    listed_days = _date_diff_days(list_date, config.as_of_date)
    if listed_days < config.min_listed_days:
        rejected["listed_days"] += 1
        return None

    bar = bars_by_code.get(ts_code)
    if bar is None:
        rejected["missing_daily_bar"] += 1
        return None
    if bool(bar.get("is_suspended", False)):
        rejected["suspended"] += 1
        return None

    amount = _float_or_zero(bar.get("amount"))
    if amount < config.min_amount:
        rejected["min_amount"] += 1
        return None

    return UniverseMember(
        universe_name=config.universe_name,
        as_of_date=config.as_of_date,
        ts_code=ts_code,
        name=str(security.get("name") or ""),
        exchange=exchange,
        list_date=list_date,
        listed_days=listed_days,
        amount=amount,
        industry=None if security.get("industry") in {None, ""} else str(security.get("industry")),
        board=board,
    )


def _latest_bars_by_code(records: list[dict[str, Any]], as_of_date: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        ts_code = str(record.get("ts_code", ""))
        trade_date = str(record.get("trade_date", ""))
        if not is_valid_ts_code(ts_code) or not is_valid_yyyymmdd(trade_date):
            continue
        if trade_date > as_of_date:
            continue
        existing = latest.get(ts_code)
        if existing is None or trade_date > str(existing.get("trade_date", "")):
            latest[ts_code] = record
    return latest


def _index_member_codes(
    records: list[dict[str, Any]],
    as_of_date: str,
    index_code: str | None,
) -> tuple[set[str], str | None]:
    eligible = [
        record
        for record in records
        if record.get("index_code") == index_code
        and is_valid_yyyymmdd(str(record.get("trade_date", "")))
        and str(record.get("trade_date")) <= as_of_date
    ]
    if not eligible:
        return set(), None
    latest_date = max(str(record["trade_date"]) for record in eligible)
    return {
        str(record["ts_code"])
        for record in eligible
        if str(record.get("trade_date")) == latest_date and is_valid_ts_code(str(record.get("ts_code", "")))
    }, latest_date


def _write_universe(
    data_dir: Path,
    config: UniverseBuildConfig,
    members: list[UniverseMember],
    total_candidates: int,
    rejected: Counter[str],
    source: str,
    latest_index_trade_date: str | None,
) -> tuple[Path, Path]:
    output_dir = data_dir / "universe"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{config.universe_name}.jsonl"
    summary_path = output_dir / f"{config.universe_name}_summary.json"

    with output_path.open("w", encoding="utf-8") as handle:
        for member in members:
            handle.write(json.dumps(asdict(member), ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    summary = {
        "universe_name": config.universe_name,
        "as_of_date": config.as_of_date,
        "output_path": str(output_path),
        "total_candidates": total_candidates,
        "selected": len(members),
        "rejected": dict(sorted(rejected.items())),
        "source": source,
        "index_code": config.index_code,
        "latest_index_trade_date": latest_index_trade_date,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return output_path, summary_path


def _date_diff_days(start_date: str, end_date: str) -> int:
    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    return max((end - start).days, 0)


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
