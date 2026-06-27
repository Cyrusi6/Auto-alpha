"""Convenience helpers for local dataset compaction and snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .pipeline import ASHARE_DATASETS
from .storage import LocalAshareStorage, StorageWriteResult


def compact_datasets(
    storage: LocalAshareStorage,
    datasets: Sequence[str] | None = None,
) -> list[StorageWriteResult]:
    selected = list(ASHARE_DATASETS if datasets is None else datasets)
    return [storage.compact_dataset(dataset) for dataset in selected if storage.dataset_exists(dataset)]


def snapshot_datasets(
    storage: LocalAshareStorage,
    datasets: Sequence[str] | None = None,
    snapshot_name: str | None = None,
) -> list[Path]:
    selected = list(ASHARE_DATASETS if datasets is None else datasets)
    return [
        storage.snapshot_dataset(dataset, snapshot_name=snapshot_name)
        for dataset in selected
        if storage.dataset_exists(dataset)
    ]
