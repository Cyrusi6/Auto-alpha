"""Point-in-time availability contracts for local A-share datasets."""

from __future__ import annotations

from data_pipeline.ashare.dataset_registry import DATASET_DEFINITIONS

from .models import DatasetAvailabilityTiming, PITDatasetContract


PIT_DATASET_CONTRACTS: dict[str, PITDatasetContract] = {
    "securities": PITDatasetContract(
        dataset="securities",
        date_field="list_date",
        entity_field="ts_code",
        availability_date_field="list_date",
        effective_date_field="list_date",
        required_fields=["ts_code", "symbol", "name", "exchange", "list_date", "list_status", "delist_date"],
        max_allowed_lag_days=None,
        timing=DatasetAvailabilityTiming.effective_date,
        allow_forward_fill=True,
        point_in_time_safe_by_default=False,
        notes="Security lifecycle depends on historical list_status/delist_date coverage.",
    ),
    "trade_calendar": PITDatasetContract(
        dataset="trade_calendar",
        date_field="trade_date",
        entity_field=None,
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        required_fields=["trade_date", "is_open"],
        max_allowed_lag_days=None,
        timing=DatasetAvailabilityTiming.before_market_open,
        allow_forward_fill=False,
        point_in_time_safe_by_default=True,
    ),
    "daily_bars": PITDatasetContract(
        dataset="daily_bars",
        date_field="trade_date",
        entity_field="ts_code",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        required_fields=["trade_date", "ts_code", "open", "high", "low", "close", "pre_close", "volume", "amount"],
        max_allowed_lag_days=1,
        timing=DatasetAvailabilityTiming.after_market_close,
        allow_forward_fill=False,
        point_in_time_safe_by_default=False,
        notes="Same-day close/amount are available only after the close.",
    ),
    "daily_basic": PITDatasetContract(
        dataset="daily_basic",
        date_field="trade_date",
        entity_field="ts_code",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        required_fields=["trade_date", "ts_code", "turnover_rate", "pe_ttm", "pb", "total_mv"],
        max_allowed_lag_days=1,
        timing=DatasetAvailabilityTiming.after_market_close,
        allow_forward_fill=True,
        point_in_time_safe_by_default=False,
    ),
    "financial_features": PITDatasetContract(
        dataset="financial_features",
        date_field="report_period",
        entity_field="ts_code",
        availability_date_field="announce_date",
        effective_date_field="report_period",
        required_fields=["ts_code", "report_period", "announce_date"],
        max_allowed_lag_days=None,
        timing=DatasetAvailabilityTiming.announced_date,
        allow_forward_fill=True,
        point_in_time_safe_by_default=True,
    ),
    "daily_limits": PITDatasetContract(
        dataset="daily_limits",
        date_field="trade_date",
        entity_field="ts_code",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        required_fields=["trade_date", "ts_code", "up_limit", "down_limit", "pre_close"],
        max_allowed_lag_days=1,
        timing=DatasetAvailabilityTiming.after_market_close,
        allow_forward_fill=False,
        point_in_time_safe_by_default=False,
    ),
    "adjustment_factors": PITDatasetContract(
        dataset="adjustment_factors",
        date_field="trade_date",
        entity_field="ts_code",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        required_fields=["trade_date", "ts_code", "adj_factor"],
        max_allowed_lag_days=None,
        timing=DatasetAvailabilityTiming.unknown,
        allow_forward_fill=True,
        point_in_time_safe_by_default=False,
        notes="Adjustment factors may be restated and require policy review.",
    ),
    "index_members": PITDatasetContract(
        dataset="index_members",
        date_field="trade_date",
        entity_field="ts_code",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        required_fields=["index_code", "trade_date", "ts_code", "weight"],
        max_allowed_lag_days=None,
        timing=DatasetAvailabilityTiming.effective_date,
        allow_forward_fill=True,
        point_in_time_safe_by_default=False,
        notes="Index weights may have publication delays and require manual review.",
    ),
    "corporate_actions": PITDatasetContract(
        dataset="corporate_actions",
        date_field="ex_date",
        entity_field="ts_code",
        availability_date_field="imp_ann_date",
        effective_date_field="ex_date",
        required_fields=["ts_code", "ann_date", "div_proc", "record_date", "ex_date", "pay_date"],
        max_allowed_lag_days=None,
        timing=DatasetAvailabilityTiming.announced_date,
        allow_forward_fill=False,
        point_in_time_safe_by_default=False,
        notes="ann_date/imp_ann_date controls availability, ex_date controls price/share effects, pay_date controls cash receipt.",
    ),
    "universe": PITDatasetContract(
        dataset="universe",
        date_field="as_of_date",
        entity_field="ts_code",
        availability_date_field="as_of_date",
        effective_date_field="as_of_date",
        required_fields=["ts_code"],
        max_allowed_lag_days=None,
        timing=DatasetAvailabilityTiming.effective_date,
        allow_forward_fill=False,
        point_in_time_safe_by_default=False,
    ),
    "factor_values": PITDatasetContract(
        dataset="factor_values",
        date_field="trade_date",
        entity_field="ts_code",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        required_fields=["factor_id", "trade_date", "ts_code", "value"],
        max_allowed_lag_days=None,
        timing=DatasetAvailabilityTiming.after_market_close,
        allow_forward_fill=False,
        point_in_time_safe_by_default=False,
    ),
    "backtest_inputs": PITDatasetContract(
        dataset="backtest_inputs",
        date_field="trade_date",
        entity_field="ts_code",
        availability_date_field="signal_date",
        effective_date_field="trade_date",
        required_fields=["trade_date", "ts_code"],
        max_allowed_lag_days=None,
        timing=DatasetAvailabilityTiming.unknown,
        allow_forward_fill=False,
        point_in_time_safe_by_default=False,
    ),
}

for _dataset, _definition in DATASET_DEFINITIONS.items():
    if _dataset in PIT_DATASET_CONTRACTS:
        continue
    _timing = DatasetAvailabilityTiming.unknown
    if _definition.availability_date_field == "ann_date":
        _timing = DatasetAvailabilityTiming.announced_date
    elif _definition.date_field == "trade_date":
        _timing = DatasetAvailabilityTiming.after_market_close
    elif _definition.effective_date_field:
        _timing = DatasetAvailabilityTiming.effective_date
    _required = sorted(
        {
            *list(_definition.primary_key),
            *[field for field in (_definition.date_field, _definition.availability_date_field, _definition.effective_date_field) if field],
        }
    )
    PIT_DATASET_CONTRACTS[_dataset] = PITDatasetContract(
        dataset=_dataset,
        date_field=_definition.date_field,
        entity_field="ts_code" if "ts_code" in _definition.fields else ("index_code" if "index_code" in _definition.fields else None),
        availability_date_field=_definition.availability_date_field,
        effective_date_field=_definition.effective_date_field,
        required_fields=_required,
        max_allowed_lag_days=None,
        timing=_timing,
        allow_forward_fill=_definition.date_field != "trade_date",
        point_in_time_safe_by_default=bool(_definition.pit_safe),
        notes="weak_pit: publication timing is uncertain and requires policy review." if _definition.weak_pit else "",
    )


_EXTENDED_PIT_OVERRIDES: dict[str, dict] = {
    "index_basic": {"availability": "list_date", "effective": "list_date", "timing": DatasetAvailabilityTiming.effective_date, "safe": False, "notes": "weak_pit: index master metadata can be revised and needs review."},
    "index_daily_bars": {"availability": "trade_date", "effective": "trade_date", "timing": DatasetAvailabilityTiming.after_market_close, "safe": False, "notes": "event_date_only: index daily data must be shifted for next-session use."},
    "index_daily_basic": {"availability": "trade_date", "effective": "trade_date", "timing": DatasetAvailabilityTiming.after_market_close, "safe": False, "notes": "event_date_only: index daily valuation data must be shifted for next-session use."},
    "industry_classification": {"availability": None, "effective": None, "timing": DatasetAvailabilityTiming.unknown, "safe": False, "notes": "weak_pit: no reliable announcement timestamp in normalized fields."},
    "industry_members": {"availability": "in_date", "effective": "in_date", "timing": DatasetAvailabilityTiming.effective_date, "safe": False, "notes": "weak_pit: membership uses effective dates; publication timing needs review."},
    "suspensions": {"availability": "ann_date", "effective": "suspend_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": False, "notes": "weak_pit: suspension announcements require announcement-date validation."},
    "name_changes": {"availability": "ann_date", "effective": "start_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": False, "notes": "weak_pit: name/status changes require announcement-date validation."},
    "new_shares": {"availability": "issue_date", "effective": "ipo_date", "timing": DatasetAvailabilityTiming.effective_date, "safe": False, "notes": "weak_pit: issue date is only a proxy for availability."},
    "income_statements": {"availability": "ann_date", "effective": "end_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date/f_ann_date availability before forward-fill."},
    "balance_sheets": {"availability": "ann_date", "effective": "end_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date/f_ann_date availability before forward-fill."},
    "cashflow_statements": {"availability": "ann_date", "effective": "end_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date/f_ann_date availability before forward-fill."},
    "earnings_forecasts": {"availability": "ann_date", "effective": "end_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date availability."},
    "earnings_express": {"availability": "ann_date", "effective": "end_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date availability."},
    "disclosure_calendar": {"availability": "ann_date", "effective": "end_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date availability."},
    "financial_audit": {"availability": "ann_date", "effective": "end_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date availability."},
    "main_business": {"availability": None, "effective": "end_date", "timing": DatasetAvailabilityTiming.unknown, "safe": False, "notes": "unsafe_missing_availability: normalized main business data lacks an announcement date."},
    "moneyflow": {"availability": "trade_date", "effective": "trade_date", "timing": DatasetAvailabilityTiming.after_market_close, "safe": False, "notes": "event_date_only: after-close data must be shifted before signal use."},
    "margin_summary": {"availability": "trade_date", "effective": "trade_date", "timing": DatasetAvailabilityTiming.after_market_close, "safe": False, "notes": "event_date_only: margin data must be shifted before signal use."},
    "margin_detail": {"availability": "trade_date", "effective": "trade_date", "timing": DatasetAvailabilityTiming.after_market_close, "safe": False, "notes": "event_date_only: margin data must be shifted before signal use."},
    "top_list": {"availability": "trade_date", "effective": "trade_date", "timing": DatasetAvailabilityTiming.after_market_close, "safe": False, "notes": "event_date_only: top-list data must be shifted before signal use."},
    "top_inst": {"availability": "trade_date", "effective": "trade_date", "timing": DatasetAvailabilityTiming.after_market_close, "safe": False, "notes": "event_date_only: top-institution data must be shifted before signal use."},
    "block_trades": {"availability": "trade_date", "effective": "trade_date", "timing": DatasetAvailabilityTiming.after_market_close, "safe": False, "notes": "event_date_only: block trade data must be shifted before signal use."},
    "hk_holdings": {"availability": "trade_date", "effective": "trade_date", "timing": DatasetAvailabilityTiming.after_market_close, "safe": False, "notes": "event_date_only: northbound holdings must be shifted before signal use."},
    "holder_number": {"availability": "ann_date", "effective": "end_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date availability."},
    "holder_trades": {"availability": "ann_date", "effective": "close_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date availability."},
    "top10_holders": {"availability": "ann_date", "effective": "end_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date availability."},
    "top10_float_holders": {"availability": "ann_date", "effective": "end_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date availability."},
    "pledge_detail": {"availability": "ann_date", "effective": "start_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date availability."},
    "pledge_stat": {"availability": None, "effective": "end_date", "timing": DatasetAvailabilityTiming.unknown, "safe": False, "notes": "unsafe_missing_availability: normalized pledge statistics lack announcement timing."},
    "repurchases": {"availability": "ann_date", "effective": "end_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date availability."},
    "share_unlocks": {"availability": "ann_date", "effective": "float_date", "timing": DatasetAvailabilityTiming.announced_date, "safe": True, "notes": "pit_safe: use ann_date availability."},
}

for _dataset, _override in _EXTENDED_PIT_OVERRIDES.items():
    _definition = DATASET_DEFINITIONS.get(_dataset)
    if _definition is None:
        continue
    _required = sorted(
        {
            *list(_definition.primary_key),
            *[
                field
                for field in (
                    _definition.date_field,
                    _override.get("availability"),
                    _override.get("effective"),
                )
                if field
            ],
        }
    )
    PIT_DATASET_CONTRACTS[_dataset] = PITDatasetContract(
        dataset=_dataset,
        date_field=_definition.date_field,
        entity_field="ts_code" if "ts_code" in _definition.fields else ("index_code" if "index_code" in _definition.fields else None),
        availability_date_field=_override.get("availability"),
        effective_date_field=_override.get("effective"),
        required_fields=_required,
        max_allowed_lag_days=None,
        timing=_override["timing"],
        allow_forward_fill=_definition.date_field != "trade_date",
        point_in_time_safe_by_default=bool(_override["safe"]),
        notes=str(_override.get("notes") or ""),
    )


def contracts_for_datasets(datasets: list[str] | None = None) -> dict[str, PITDatasetContract]:
    if datasets is None:
        return dict(PIT_DATASET_CONTRACTS)
    return {dataset: PIT_DATASET_CONTRACTS[dataset] for dataset in datasets if dataset in PIT_DATASET_CONTRACTS}
