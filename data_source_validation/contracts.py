"""Dataset contracts for the currently implemented A-share providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from data_pipeline.ashare.storage import DATASET_PRIMARY_KEYS


@dataclass(frozen=True)
class DatasetContract:
    dataset: str
    api_name: str
    request_fields: list[str]
    local_fields: list[str]
    primary_key: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DATASET_CONTRACTS: dict[str, DatasetContract] = {
    "securities": DatasetContract(
        dataset="securities",
        api_name="stock_basic",
        request_fields=["ts_code", "symbol", "name", "exchange", "list_date", "industry", "market"],
        local_fields=["ts_code", "symbol", "name", "exchange", "list_date", "industry", "board", "is_st"],
        primary_key=list(DATASET_PRIMARY_KEYS["securities"]),
    ),
    "trade_calendar": DatasetContract(
        dataset="trade_calendar",
        api_name="trade_cal",
        request_fields=["cal_date", "is_open", "pretrade_date"],
        local_fields=["trade_date", "is_open", "prev_trade_date", "next_trade_date"],
        primary_key=list(DATASET_PRIMARY_KEYS["trade_calendar"]),
    ),
    "daily_bars": DatasetContract(
        dataset="daily_bars",
        api_name="daily",
        request_fields=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"],
        local_fields=["trade_date", "ts_code", "open", "high", "low", "close", "pre_close", "volume", "amount", "adj_factor", "limit_up", "limit_down", "is_suspended"],
        primary_key=list(DATASET_PRIMARY_KEYS["daily_bars"]),
    ),
    "daily_basic": DatasetContract(
        dataset="daily_basic",
        api_name="daily_basic",
        request_fields=["ts_code", "trade_date", "turnover_rate", "volume_ratio", "pe_ttm", "pb", "ps_ttm", "total_mv", "circ_mv"],
        local_fields=["trade_date", "ts_code", "turnover_rate", "volume_ratio", "pe_ttm", "pb", "ps_ttm", "total_mv", "circ_mv"],
        primary_key=list(DATASET_PRIMARY_KEYS["daily_basic"]),
    ),
    "financial_features": DatasetContract(
        dataset="financial_features",
        api_name="fina_indicator",
        request_fields=["ts_code", "end_date", "ann_date", "roe", "roa", "grossprofit_margin", "or_yoy", "netprofit_yoy", "debt_to_assets", "ocfps"],
        local_fields=["ts_code", "report_period", "announce_date", "roe", "roa", "gross_margin", "revenue_yoy", "net_profit_yoy", "debt_to_asset", "operating_cashflow"],
        primary_key=list(DATASET_PRIMARY_KEYS["financial_features"]),
    ),
    "daily_limits": DatasetContract(
        dataset="daily_limits",
        api_name="stk_limit",
        request_fields=["trade_date", "ts_code", "up_limit", "down_limit", "pre_close"],
        local_fields=["trade_date", "ts_code", "up_limit", "down_limit", "pre_close"],
        primary_key=list(DATASET_PRIMARY_KEYS["daily_limits"]),
    ),
    "adjustment_factors": DatasetContract(
        dataset="adjustment_factors",
        api_name="adj_factor",
        request_fields=["ts_code", "trade_date", "adj_factor"],
        local_fields=["trade_date", "ts_code", "adj_factor"],
        primary_key=list(DATASET_PRIMARY_KEYS["adjustment_factors"]),
    ),
    "index_members": DatasetContract(
        dataset="index_members",
        api_name="index_weight",
        request_fields=["index_code", "con_code", "trade_date", "weight"],
        local_fields=["index_code", "trade_date", "ts_code", "weight"],
        primary_key=list(DATASET_PRIMARY_KEYS["index_members"]),
    ),
}


def contracts_for_datasets(datasets: list[str] | None = None) -> dict[str, DatasetContract]:
    if datasets is None:
        return dict(DATASET_CONTRACTS)
    return {dataset: DATASET_CONTRACTS[dataset] for dataset in datasets if dataset in DATASET_CONTRACTS}
