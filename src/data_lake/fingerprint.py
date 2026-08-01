"""Dataset fingerprinting for local JSONL A-share data."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from data_pipeline.ashare.pipeline import ASHARE_DATASETS
from data_pipeline.ashare.stats import compute_dataset_stats
from data_pipeline.ashare.storage import DATASET_PRIMARY_KEYS, LocalAshareStorage

from .models import DatasetFingerprint


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def hash_file_streaming(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_dataset(storage: LocalAshareStorage, dataset_name: str) -> DatasetFingerprint:
    path = storage.dataset_path(dataset_name)
    key_fields = list(DATASET_PRIMARY_KEYS.get(dataset_name, ()))
    if not path.exists():
        return DatasetFingerprint(
            dataset=dataset_name,
            path=str(path),
            records=0,
            size_bytes=0,
            sha256="",
            schema_version="1.0",
            primary_key_fields=key_fields,
            primary_key_count=0,
            duplicate_key_count=0,
            first_date=None,
            last_date=None,
            ts_code_count=0,
            null_counts={},
            field_hash="",
            updated_at=utc_now(),
            missing=True,
        )
    stats = compute_dataset_stats(storage, dataset_name)
    records = storage.read_dataset(dataset_name)
    fields = sorted({field for record in records for field in record})
    field_hash = hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode("utf-8")).hexdigest()
    return DatasetFingerprint(
        dataset=dataset_name,
        path=str(path),
        records=stats.records,
        size_bytes=stats.file_size_bytes,
        sha256=hash_file_streaming(path),
        schema_version="1.0",
        primary_key_fields=key_fields,
        primary_key_count=stats.unique_keys,
        duplicate_key_count=stats.duplicate_keys,
        first_date=stats.first_trade_date,
        last_date=stats.last_trade_date,
        ts_code_count=stats.ts_code_count,
        null_counts=stats.null_counts,
        field_hash=field_hash,
        updated_at=stats.updated_at,
        missing=False,
    )


def fingerprint_data_dir(data_dir: str | Path, datasets: Iterable[str] | None = None) -> list[DatasetFingerprint]:
    storage = LocalAshareStorage(data_dir)
    selected = list(ASHARE_DATASETS if datasets is None else datasets)
    return [fingerprint_dataset(storage, dataset) for dataset in selected]


def content_hash_for_fingerprints(fingerprints: list[DatasetFingerprint]) -> str:
    payload = [
        {
            "dataset": item.dataset,
            "sha256": item.sha256,
            "records": item.records,
            "missing": item.missing,
        }
        for item in sorted(fingerprints, key=lambda item: item.dataset)
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
