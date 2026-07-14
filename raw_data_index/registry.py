"""Dataset grouping and date-field helpers for raw data indexes."""

from __future__ import annotations

from data_pipeline.ashare.dataset_registry import DATASET_DEFINITIONS, DATASET_PRIMARY_KEYS, FULL_RESEARCH_DATASETS

TRADE_DATE_PARTITION_DATASETS = {
    "daily_bars",
    "daily_basic",
    "daily_limits",
    "adjustment_factors",
    "moneyflow",
    "margin_summary",
    "margin_detail",
    "top_list",
    "top_inst",
    "block_trades",
    "hk_holdings",
    "index_daily_bars",
    "index_daily_basic",
}

TS_CODE_PARTITION_DATASETS = {
    "financial_features",
    "income_statements",
    "balance_sheets",
    "cashflow_statements",
    "earnings_forecasts",
    "earnings_express",
    "disclosure_calendar",
    "financial_audit",
    "main_business",
    "holder_number",
    "holder_trades",
    "top10_holders",
    "top10_float_holders",
    "pledge_detail",
    "pledge_stat",
    "repurchases",
    "share_unlocks",
}

INDEX_CODE_PARTITION_DATASETS = {
    "index_members",
    "industry_members",
}

STATIC_STATUS_DATASETS = {
    "securities",
    "trade_calendar",
    "index_basic",
    "industry_classification",
    "suspensions",
    "st_status_daily",
    "name_changes",
    "new_shares",
    "corporate_actions",
}

DATE_FIELD_CANDIDATES = (
    "trade_date",
    "ann_date",
    "f_ann_date",
    "end_date",
    "report_period",
    "list_date",
    "delist_date",
    "in_date",
    "out_date",
    "start_date",
    "ipo_date",
    "issue_date",
    "ex_date",
    "record_date",
    "pay_date",
    "unlock_date",
)


def default_datasets() -> list[str]:
    return list(FULL_RESEARCH_DATASETS)


def primary_key_fields(dataset: str) -> list[str]:
    fields = DATASET_PRIMARY_KEYS.get(dataset)
    if fields:
        return list(fields)
    definition = DATASET_DEFINITIONS.get(dataset)
    return list(definition.primary_key) if definition else []


def primary_date_field(dataset: str) -> str | None:
    definition = DATASET_DEFINITIONS.get(dataset)
    if definition and definition.date_field:
        return definition.date_field
    if dataset == "securities":
        return "list_date"
    if dataset == "trade_calendar":
        return "trade_date"
    return "trade_date"


def availability_date_field(dataset: str) -> str | None:
    definition = DATASET_DEFINITIONS.get(dataset)
    return definition.availability_date_field if definition else None


def partition_type_for_dataset(dataset: str, granularity: str) -> str:
    if granularity == "none":
        return "dataset"
    if dataset in TRADE_DATE_PARTITION_DATASETS:
        return "trade_date_day" if granularity == "daily" else "trade_date_month"
    if dataset in TS_CODE_PARTITION_DATASETS:
        return "ts_code_bucket"
    if dataset in INDEX_CODE_PARTITION_DATASETS:
        return "index_code"
    if dataset in STATIC_STATUS_DATASETS:
        return "dataset"
    return "trade_date_day" if granularity == "daily" else "date_month"
