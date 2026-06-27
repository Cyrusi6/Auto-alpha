"""A-share data models, configuration, and validation helpers."""

from .config import AShareDataConfig
from .manager import AShareDataManager, SyncDatasetResult, SyncResult
from .pipeline import ASHARE_DATASETS, DatasetPlan, PipelinePlan, build_pipeline_plan
from .providers import AShareDataProvider, SampleAShareDataProvider, create_ashare_provider
from .schema import (
    DailyBar,
    DailyBasic,
    FactorMetadata,
    FactorValue,
    FinancialFeature,
    Security,
    TradeCalendarRecord,
)
from .storage import LocalAshareStorage, StorageWriteResult

__all__ = [
    "ASHARE_DATASETS",
    "AShareDataManager",
    "AShareDataConfig",
    "AShareDataProvider",
    "DatasetPlan",
    "DailyBar",
    "DailyBasic",
    "FactorMetadata",
    "FactorValue",
    "FinancialFeature",
    "LocalAshareStorage",
    "PipelinePlan",
    "SampleAShareDataProvider",
    "Security",
    "StorageWriteResult",
    "SyncDatasetResult",
    "SyncResult",
    "TradeCalendarRecord",
    "build_pipeline_plan",
    "create_ashare_provider",
]
