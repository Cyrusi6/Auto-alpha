"""A-share data manager entry point."""

from .ashare.manager import AShareDataManager, SyncDatasetResult, SyncResult

__all__ = ["AShareDataManager", "SyncDatasetResult", "SyncResult"]
