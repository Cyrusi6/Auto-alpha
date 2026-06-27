"""Production sync plan construction for A-share datasets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Sequence

from .config import AShareDataConfig
from .pipeline import ASHARE_DATASETS
from .validators import is_valid_yyyymmdd


WINDOWED_DATASETS = {
    "daily_bars",
    "daily_basic",
    "financial_features",
    "daily_limits",
    "adjustment_factors",
}


@dataclass(frozen=True)
class SyncJob:
    job_id: str
    dataset: str
    provider: str
    start_date: str | None = None
    end_date: str | None = None
    index_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SyncPlan:
    plan_id: str
    provider: str
    datasets: list[str]
    start_date: str
    end_date: str
    chunk_days: int
    index_codes: list[str]
    jobs: list[SyncJob]

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "provider": self.provider,
            "datasets": self.datasets,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "chunk_days": self.chunk_days,
            "index_codes": self.index_codes,
            "jobs": [job.to_dict() for job in self.jobs],
        }


def build_sync_plan(
    config: AShareDataConfig,
    datasets: Sequence[str] | None = None,
    chunk_days: int = 30,
    index_codes: Sequence[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> SyncPlan:
    selected = list(ASHARE_DATASETS if datasets is None else datasets)
    unsupported = sorted(set(selected) - set(ASHARE_DATASETS))
    if unsupported:
        raise ValueError(f"Unsupported A-share datasets: {', '.join(unsupported)}")

    plan_start = start_date or config.start_date
    plan_end = end_date or config.end_date or plan_start
    if not is_valid_yyyymmdd(plan_start) or not is_valid_yyyymmdd(plan_end):
        raise ValueError("sync plan dates must be real YYYYMMDD dates")
    if plan_start > plan_end:
        raise ValueError("sync plan start_date must be <= end_date")

    codes = list(index_codes or config.index_codes)
    if not codes:
        raise ValueError("sync plan index_codes must include at least one code")

    windows = split_date_windows(plan_start, plan_end, chunk_days)
    jobs: list[SyncJob] = []
    for dataset in selected:
        if dataset == "securities":
            jobs.append(_make_job(config.provider, dataset))
        elif dataset == "trade_calendar":
            jobs.append(_make_job(config.provider, dataset, plan_start, plan_end))
        elif dataset == "index_members":
            for index_code in codes:
                for window_start, window_end in windows:
                    jobs.append(
                        _make_job(
                            config.provider,
                            dataset,
                            window_start,
                            window_end,
                            index_code=index_code,
                        )
                    )
        elif dataset in WINDOWED_DATASETS:
            for window_start, window_end in windows:
                jobs.append(_make_job(config.provider, dataset, window_start, window_end))
        else:
            jobs.append(_make_job(config.provider, dataset, plan_start, plan_end))

    payload = {
        "provider": config.provider,
        "datasets": selected,
        "start_date": plan_start,
        "end_date": plan_end,
        "chunk_days": chunk_days,
        "index_codes": codes,
        "jobs": [job.to_dict() for job in jobs],
    }
    plan_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return SyncPlan(
        plan_id=f"plan_{plan_hash[:16]}",
        provider=config.provider,
        datasets=selected,
        start_date=plan_start,
        end_date=plan_end,
        chunk_days=chunk_days,
        index_codes=codes,
        jobs=jobs,
    )


def split_date_windows(
    start_date: str,
    end_date: str,
    chunk_days: int,
    trade_dates: Sequence[str] | None = None,
) -> list[tuple[str, str]]:
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive")
    if not is_valid_yyyymmdd(start_date) or not is_valid_yyyymmdd(end_date):
        raise ValueError("date windows require real YYYYMMDD dates")
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")

    if trade_dates is not None:
        selected = sorted(date for date in trade_dates if start_date <= date <= end_date)
        return [
            (selected[index], selected[min(index + chunk_days - 1, len(selected) - 1)])
            for index in range(0, len(selected), chunk_days)
        ]

    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    windows: list[tuple[str, str]] = []
    current = start
    while current <= end:
        window_end = min(current + timedelta(days=chunk_days - 1), end)
        windows.append((current.strftime("%Y%m%d"), window_end.strftime("%Y%m%d")))
        current = window_end + timedelta(days=1)
    return windows


def _make_job(
    provider: str,
    dataset: str,
    start_date: str | None = None,
    end_date: str | None = None,
    index_code: str | None = None,
) -> SyncJob:
    payload = {
        "provider": provider,
        "dataset": dataset,
        "start_date": start_date,
        "end_date": end_date,
        "index_code": index_code,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return SyncJob(
        job_id=f"job_{digest[:16]}",
        dataset=dataset,
        provider=provider,
        start_date=start_date,
        end_date=end_date,
        index_code=index_code,
    )
