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
    AdjustmentFactor,
    DailyBar,
    DailyBasic,
    DailyLimit,
    FactorMetadata,
    FactorValue,
    FinancialFeature,
    IndexMember,
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
from .stats import DatasetStats, compute_all_dataset_stats, compute_dataset_stats, write_dataset_stats
from .storage import LocalAshareStorage, StorageWriteResult
from .sync_plan import SyncJob, SyncPlan, build_sync_plan, split_date_windows

__all__ = [
    "ASHARE_DATASETS",
    "AShareDataManager",
    "AShareDataConfig",
    "AShareDataProvider",
    "AdjustmentFactor",
    "DatasetPlan",
    "DatasetQualitySummary",
    "DatasetSyncState",
    "DatasetStats",
    "DailyBar",
    "DailyBasic",
    "DailyLimit",
    "DataQualityIssue",
    "DataQualityReport",
    "FactorMetadata",
    "FactorValue",
    "FinancialFeature",
    "IndexMember",
    "LocalAshareStorage",
    "PipelinePlan",
    "PipelineSyncState",
    "SampleAShareDataProvider",
    "Security",
    "StorageWriteResult",
    "SyncJob",
    "SyncPlan",
    "SyncDatasetResult",
    "SyncResult",
    "TradeCalendarRecord",
    "TushareAShareDataProvider",
    "TushareApiError",
    "TushareHttpClient",
    "build_pipeline_plan",
    "build_sync_plan",
    "compute_all_dataset_stats",
    "compute_dataset_stats",
    "create_ashare_provider",
    "default_pipeline_state_path",
    "load_pipeline_state",
    "save_pipeline_state",
    "validate_all_datasets",
    "validate_dataset",
    "write_quality_report",
    "split_date_windows",
    "write_dataset_stats",
]
