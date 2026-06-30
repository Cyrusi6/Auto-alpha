"""Dataset-specific backfill chunk strategy helpers."""

from __future__ import annotations


PRODUCTION_DAILY_CHUNK_DAYS = {
    "daily_bars": 1,
    "daily_basic": 1,
    "daily_limits": 1,
    "adjustment_factors": 1,
    "corporate_actions": 1,
    "financial_features": 30,
    "index_members": 30,
    "trade_calendar": 365,
}


def dataset_chunk_days_for_strategy(strategy: str, default_chunk_days: int = 30) -> dict[str, int]:
    if strategy == "production_daily":
        return dict(PRODUCTION_DAILY_CHUNK_DAYS)
    if strategy in {"uniform", "", None}:  # type: ignore[comparison-overlap]
        return {}
    raise ValueError(f"unsupported chunk strategy: {strategy}")


def parse_dataset_chunk_days(value: str | None) -> dict[str, int]:
    if not value:
        return {}
    parsed: dict[str, int] = {}
    for item in value.split(","):
        if not item.strip():
            continue
        if "=" not in item:
            raise ValueError("dataset chunk overrides must use dataset=days")
        dataset, days = item.split("=", 1)
        parsed[dataset.strip()] = max(1, int(days.strip()))
    return parsed
