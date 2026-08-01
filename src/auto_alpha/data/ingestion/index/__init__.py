"""Sidecar raw JSONL index utilities for governed A-share datasets."""

from .models import (
    RawDataIndexManifest,
    RawDataIndexReport,
    RawDataIndexStatus,
    RawDataIndexValidationReport,
    RawDatasetIndex,
    RawPartitionRecord,
)
from .scanner import build_raw_data_index
from .validator import validate_raw_data_index

__all__ = [
    "RawDataIndexManifest",
    "RawDataIndexReport",
    "RawDataIndexStatus",
    "RawDataIndexValidationReport",
    "RawDatasetIndex",
    "RawPartitionRecord",
    "build_raw_data_index",
    "validate_raw_data_index",
]
