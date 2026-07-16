"""Built-in real-data operation profiles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from data_pipeline.ashare.dataset_registry import (
    CORE_DATASETS,
    EXPANDED_INDEX_CODES,
    FINANCIAL_STATEMENT_DATASETS,
    FLOW_MARGIN_TRADING_DATASETS,
    FULL_RESEARCH_DATASETS,
    HOLDER_EVENT_RISK_DATASETS,
    INDEX_INDUSTRY_STATUS_DATASETS,
)
from data_pipeline.ashare.pipeline import ASHARE_DATASETS

from .models import RealDataProfile, RealDataRunMode


FULL_DATASETS = list(CORE_DATASETS)
FULL_RESEARCH_DATASETS_LIST = list(FULL_RESEARCH_DATASETS)


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


def get_real_data_profile(name: str) -> RealDataProfile:
    if name == "sample_offline_small":
        return _with_id(
            RealDataProfile(
                profile_id="",
                profile_name=name,
                provider="sample",
                api_url=None,
                datasets=FULL_DATASETS,
                start_date="20240102",
                end_date="20240104",
                index_codes=["000300.SH"],
                security_list_statuses=["L", "D", "P"],
                chunk_strategy="uniform",
                dataset_chunk_days={},
                mode=RealDataRunMode.offline_sample,
                storage_mode="append",
                freeze_mode="copy",
                matrix_refresh_mode="skip_if_fresh",
                allow_network=False,
                require_token=False,
            )
        )
    if name == "fake_tushare_small":
        return _with_id(
            RealDataProfile(
                profile_id="",
                profile_name=name,
                provider="tushare",
                api_url="https://api.tushare.pro",
                datasets=FULL_RESEARCH_DATASETS_LIST,
                start_date="20240102",
                end_date="20240104",
                index_codes=list(EXPANDED_INDEX_CODES),
                security_list_statuses=["L", "D", "P"],
                chunk_strategy="uniform",
                mode=RealDataRunMode.fake_tushare,
                storage_mode="append",
                freeze_mode="copy",
                matrix_refresh_mode="skip_if_fresh",
            )
        )
    if name == "tushare_online_smoke":
        return _with_id(
            RealDataProfile(
                profile_id="",
                profile_name=name,
                provider="tushare",
                api_url="https://api.tushare.pro",
                datasets=["securities", "trade_calendar", "daily_bars", "daily_basic", "daily_limits", "adjustment_factors", "index_members"],
                start_date="20240102",
                end_date="20240104",
                index_codes=["000300.SH"],
                security_list_statuses=["L", "D", "P"],
                chunk_strategy="uniform",
                max_requests=20,
                rate_limit_per_minute=150,
                require_token=True,
                allow_network=False,
                mode=RealDataRunMode.online_tushare_smoke,
                storage_mode="append",
                freeze_mode="manifest_only",
            )
        )
    if name in {"tushare_full_ashare_2010_2026", "tushare_full_ashare_incremental"}:
        return _with_id(
            RealDataProfile(
                profile_id="",
                profile_name=name,
                provider="tushare",
                api_url="https://ts.gyzcloud.top/api",
                datasets=FULL_DATASETS,
                start_date="20100101",
                end_date="20260630",
                index_codes=["000300.SH", "000905.SH", "000852.SH"],
                security_list_statuses=["L", "D", "P"],
                chunk_strategy="production_daily",
                dataset_chunk_days=dict(PRODUCTION_DAILY_CHUNK_DAYS),
                max_requests=None,
                rate_limit_per_minute=150,
                require_token=True,
                allow_network=False,
                mode=RealDataRunMode.online_tushare_incremental if name.endswith("incremental") else RealDataRunMode.online_tushare_full_backfill,
                storage_mode="append",
                freeze_mode="manifest_only",
                matrix_refresh_mode="skip_if_fresh",
                priority=10,
                metadata={"token_expiry": "2026-07-07 20:24"},
            )
        )
    if name in {
        "core_daily",
        "index_industry_status",
        "financial_statements",
        "flow_margin_trading",
        "holder_event_risk",
        "full_research_data",
    }:
        datasets_by_profile = {
            "core_daily": list(CORE_DATASETS),
            "index_industry_status": list(INDEX_INDUSTRY_STATUS_DATASETS),
            "financial_statements": list(FINANCIAL_STATEMENT_DATASETS),
            "flow_margin_trading": list(FLOW_MARGIN_TRADING_DATASETS),
            "holder_event_risk": list(HOLDER_EVENT_RISK_DATASETS),
            "full_research_data": FULL_RESEARCH_DATASETS_LIST,
        }
        return _with_id(
            RealDataProfile(
                profile_id="",
                profile_name=name,
                provider="tushare",
                api_url="https://ts.gyzcloud.top/api",
                datasets=datasets_by_profile[name],
                start_date="20100101",
                end_date="20260630",
                index_codes=list(EXPANDED_INDEX_CODES),
                security_list_statuses=["L", "D", "P"],
                chunk_strategy="production_daily",
                dataset_chunk_days=dict(PRODUCTION_DAILY_CHUNK_DAYS),
                max_requests=None,
                rate_limit_per_minute=150,
                require_token=True,
                allow_network=False,
                mode=RealDataRunMode.online_tushare_full_backfill,
                storage_mode="append",
                freeze_mode="manifest_only",
                matrix_refresh_mode="skip_if_fresh",
                priority=20,
                metadata={"token_expiry": "2026-07-07 20:24"},
            )
        )
    raise ValueError(f"unknown real data profile: {name}")


def load_profile_json(path: str | Path) -> RealDataProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    base = get_real_data_profile(str(payload.get("profile_name") or "sample_offline_small"))
    values = base.to_dict()
    values.update(payload)
    values["profile_id"] = ""
    return _with_id(RealDataProfile(**values))


def profile_with_overrides(profile: RealDataProfile, **overrides: Any) -> RealDataProfile:
    clean = {key: value for key, value in overrides.items() if value not in (None, "", [])}
    return _with_id(replace(profile, **clean, profile_id=""))


def _with_id(profile: RealDataProfile) -> RealDataProfile:
    payload = profile.to_dict()
    payload.pop("profile_id", None)
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return replace(profile, profile_id=f"rdp_{digest[:16]}")


def supported_datasets_or_default(value: list[str] | None) -> list[str]:
    selected = list(FULL_DATASETS if not value else value)
    unsupported = sorted(set(selected) - set(ASHARE_DATASETS))
    if unsupported:
        raise ValueError(f"unsupported datasets: {', '.join(unsupported)}")
    return selected
