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
    "index_daily_bars": 1,
    "index_daily_basic": 1,
    "suspensions": 1,
    "new_shares": 30,
    "income_statements": 30,
    "balance_sheets": 30,
    "cashflow_statements": 30,
    "earnings_forecasts": 30,
    "earnings_express": 30,
    "disclosure_calendar": 30,
    "financial_audit": 30,
    "main_business": 30,
    "moneyflow": 1,
    "margin_summary": 30,
    "margin_detail": 1,
    "top_list": 1,
    "top_inst": 1,
    "block_trades": 1,
    "holder_number": 30,
    "holder_trades": 30,
    "top10_holders": 30,
    "top10_float_holders": 30,
    "pledge_detail": 30,
    "pledge_stat": 30,
    "repurchases": 30,
    "share_unlocks": 30,
    "hk_holdings": 1,
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
