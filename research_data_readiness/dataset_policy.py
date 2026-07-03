"""Dataset tiering and PIT policy for real-data research readiness."""

from __future__ import annotations

from dataclasses import dataclass

from point_in_time.contracts import PIT_DATASET_CONTRACTS
from point_in_time.models import DatasetAvailabilityTiming, PITDatasetContract

from .models import DatasetPitSafety, DatasetResearchTier


CORE_REQUIRED = (
    "securities",
    "trade_calendar",
    "daily_bars",
    "daily_basic",
    "adjustment_factors",
    "daily_limits",
    "index_members",
    "corporate_actions",
    "financial_features",
)

INDEX_INDUSTRY_REQUIRED = (
    "index_basic",
    "index_daily_bars",
    "index_daily_basic",
    "industry_classification",
    "industry_members",
    "suspensions",
    "name_changes",
    "new_shares",
)

FINANCIAL_REQUIRED = (
    "income_statements",
    "balance_sheets",
    "cashflow_statements",
    "earnings_forecasts",
    "earnings_express",
    "disclosure_calendar",
    "financial_audit",
    "main_business",
)

ALPHA_OPTIONAL = (
    "moneyflow",
    "margin_summary",
    "margin_detail",
    "top_list",
    "top_inst",
    "block_trades",
    "hk_holdings",
)

EVENT_OPTIONAL = (
    "holder_number",
    "holder_trades",
    "top10_holders",
    "top10_float_holders",
    "pledge_detail",
    "pledge_stat",
    "repurchases",
    "share_unlocks",
)

ALL_RESEARCH_DATASETS = (
    *CORE_REQUIRED,
    *INDEX_INDUSTRY_REQUIRED,
    *FINANCIAL_REQUIRED,
    *ALPHA_OPTIONAL,
    *EVENT_OPTIONAL,
)

_TIER_BY_DATASET = {
    **{dataset: DatasetResearchTier.core_required for dataset in CORE_REQUIRED},
    **{dataset: DatasetResearchTier.index_industry_required for dataset in INDEX_INDUSTRY_REQUIRED},
    **{dataset: DatasetResearchTier.financial_required for dataset in FINANCIAL_REQUIRED},
    **{dataset: DatasetResearchTier.alpha_optional for dataset in ALPHA_OPTIONAL},
    **{dataset: DatasetResearchTier.event_optional for dataset in EVENT_OPTIONAL},
}

PIT_SAFETY_OVERRIDES = {
    "securities": DatasetPitSafety.weak_pit,
    "daily_bars": DatasetPitSafety.event_date_only,
    "daily_basic": DatasetPitSafety.event_date_only,
    "daily_limits": DatasetPitSafety.event_date_only,
    "adjustment_factors": DatasetPitSafety.weak_pit,
    "index_members": DatasetPitSafety.weak_pit,
    "corporate_actions": DatasetPitSafety.pit_safe,
    "index_basic": DatasetPitSafety.weak_pit,
    "index_daily_bars": DatasetPitSafety.event_date_only,
    "index_daily_basic": DatasetPitSafety.event_date_only,
    "industry_classification": DatasetPitSafety.weak_pit,
    "industry_members": DatasetPitSafety.weak_pit,
    "suspensions": DatasetPitSafety.weak_pit,
    "name_changes": DatasetPitSafety.weak_pit,
    "new_shares": DatasetPitSafety.weak_pit,
    "main_business": DatasetPitSafety.unsafe_missing_availability,
    "moneyflow": DatasetPitSafety.event_date_only,
    "margin_summary": DatasetPitSafety.event_date_only,
    "margin_detail": DatasetPitSafety.event_date_only,
    "top_list": DatasetPitSafety.event_date_only,
    "top_inst": DatasetPitSafety.event_date_only,
    "block_trades": DatasetPitSafety.event_date_only,
    "hk_holdings": DatasetPitSafety.event_date_only,
    "pledge_stat": DatasetPitSafety.unsafe_missing_availability,
}


@dataclass(frozen=True)
class DatasetResearchPolicy:
    dataset: str
    tier: str
    pit_safety: str
    availability_field: str | None
    timing: str
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "tier": self.tier,
            "pit_safety": self.pit_safety,
            "availability_field": self.availability_field,
            "timing": self.timing,
            "notes": self.notes,
        }


def dataset_tier(dataset: str) -> str:
    return _TIER_BY_DATASET.get(dataset, DatasetResearchTier.broker_irrelevant)


def dataset_policy(dataset: str) -> DatasetResearchPolicy:
    contract = PIT_DATASET_CONTRACTS.get(dataset)
    return DatasetResearchPolicy(
        dataset=dataset,
        tier=dataset_tier(dataset),
        pit_safety=dataset_pit_safety(dataset, contract),
        availability_field=contract.availability_date_field if contract else None,
        timing=contract.timing if contract else DatasetAvailabilityTiming.unknown,
        notes=contract.notes if contract else "",
    )


def dataset_pit_safety(dataset: str, contract: PITDatasetContract | None = None) -> str:
    if dataset in PIT_SAFETY_OVERRIDES:
        return PIT_SAFETY_OVERRIDES[dataset]
    contract = contract or PIT_DATASET_CONTRACTS.get(dataset)
    if contract is None:
        return DatasetPitSafety.unknown
    if contract.point_in_time_safe_by_default and contract.availability_date_field:
        return DatasetPitSafety.pit_safe
    if not contract.availability_date_field:
        return DatasetPitSafety.unsafe_missing_availability
    if contract.timing in {DatasetAvailabilityTiming.after_market_close, DatasetAvailabilityTiming.next_trade_day_open}:
        return DatasetPitSafety.event_date_only
    if "weak_pit" in (contract.notes or ""):
        return DatasetPitSafety.weak_pit
    return DatasetPitSafety.weak_pit


def policies_for_datasets(datasets: list[str] | None = None) -> list[DatasetResearchPolicy]:
    selected = datasets or list(ALL_RESEARCH_DATASETS)
    return [dataset_policy(dataset) for dataset in selected]
