"""A-share data models, configuration, and validation helpers."""

from .config import AShareDataConfig
from .manager import AShareDataManager, SyncDatasetResult, SyncResult
from .pipeline import ASHARE_DATASETS, DatasetPlan, PipelinePlan, build_pipeline_plan
from .providers import (
    AShareDataProvider,
    SampleAShareDataProvider,
    TushareAShareDataProvider,
    TushareApiError,
    TushareHttpClient,
    create_ashare_provider,
)
from .quality import (
    DataQualityIssue,
    DataQualityReport,
    DatasetQualitySummary,
    validate_all_datasets,
    validate_dataset,
    write_quality_report,
)
from .schema import (
    DailyBar,
    DailyBasic,
    FactorMetadata,
    FactorValue,
    FinancialFeature,
    Security,
    TradeCalendarRecord,
)
from .state import (
    DatasetSyncState,
    PipelineSyncState,
    default_pipeline_state_path,
    load_pipeline_state,
    save_pipeline_state,
)
from .storage import LocalAshareStorage, StorageWriteResult

__all__ = [
    "ASHARE_DATASETS",
    "AShareDataManager",
    "AShareDataConfig",
    "AShareDataProvider",
    "DatasetPlan",
    "DatasetQualitySummary",
    "DatasetSyncState",
    "DailyBar",
    "DailyBasic",
    "DataQualityIssue",
    "DataQualityReport",
    "FactorMetadata",
    "FactorValue",
    "FinancialFeature",
    "LocalAshareStorage",
    "PipelinePlan",
    "PipelineSyncState",
    "SampleAShareDataProvider",
    "Security",
    "StorageWriteResult",
    "SyncDatasetResult",
    "SyncResult",
    "TradeCalendarRecord",
    "TushareAShareDataProvider",
    "TushareApiError",
    "TushareHttpClient",
    "build_pipeline_plan",
    "create_ashare_provider",
    "default_pipeline_state_path",
    "load_pipeline_state",
    "save_pipeline_state",
    "validate_all_datasets",
    "validate_dataset",
    "write_quality_report",
]
