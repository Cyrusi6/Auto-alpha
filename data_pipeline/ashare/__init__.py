"""A-share data models, configuration, and validation helpers."""

from .config import AShareDataConfig
from .pipeline import DatasetPlan, PipelinePlan, build_pipeline_plan
from .schema import (
    DailyBar,
    DailyBasic,
    FactorMetadata,
    FactorValue,
    FinancialFeature,
    Security,
    TradeCalendarRecord,
)

__all__ = [
    "AShareDataConfig",
    "DatasetPlan",
    "DailyBar",
    "DailyBasic",
    "FactorMetadata",
    "FactorValue",
    "FinancialFeature",
    "PipelinePlan",
    "Security",
    "TradeCalendarRecord",
    "build_pipeline_plan",
]
