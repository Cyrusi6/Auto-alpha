"""Point-in-time governance helpers for local A-share artifacts."""

from .asof import asof_join, compute_feature_cutoff_date
from .contracts import PIT_DATASET_CONTRACTS, contracts_for_datasets
from .models import (
    ActiveSecurityMask,
    DataAvailabilityRecord,
    DatasetAvailabilityTiming,
    PITDatasetContract,
    PITDatasetManifest,
    PITValidationIssue,
    PITValidationReport,
    SecurityLifecycleRecord,
    SurvivorshipBiasReport,
)
from .security_master import build_active_security_mask, build_security_lifecycle
from .validator import validate_point_in_time_data

__all__ = [
    "ActiveSecurityMask",
    "DataAvailabilityRecord",
    "DatasetAvailabilityTiming",
    "PITDatasetContract",
    "PITDatasetManifest",
    "PITValidationIssue",
    "PITValidationReport",
    "PIT_DATASET_CONTRACTS",
    "SecurityLifecycleRecord",
    "SurvivorshipBiasReport",
    "asof_join",
    "build_active_security_mask",
    "build_security_lifecycle",
    "compute_feature_cutoff_date",
    "contracts_for_datasets",
    "validate_point_in_time_data",
]
