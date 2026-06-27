"""Field coverage diagnostics for governed A-share datasets."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from data_pipeline.ashare.storage import DATASET_PRIMARY_KEYS, LocalAshareStorage

from .contracts import contracts_for_datasets
from .models import FieldCoverageResult


def analyze_field_coverage(data_dir: str | Path, datasets: Iterable[str] | None = None) -> list[FieldCoverageResult]:
    storage = LocalAshareStorage(data_dir)
    results: list[FieldCoverageResult] = []
    for contract in contracts_for_datasets(list(datasets) if datasets is not None else None).values():
        records = storage.read_dataset(contract.dataset)
        expected = list(contract.local_fields)
        present = sorted({field for record in records for field in record})
        missing = sorted(set(expected) - set(present))
        null_counts = {
            field: sum(record.get(field) in {None, ""} for record in records)
            for field in expected
        }
        null_ratios = {
            field: (count / len(records) if records else 0.0)
            for field, count in null_counts.items()
        }
        dates = sorted(
            str(record[field])
            for record in records
            for field in ("trade_date", "list_date", "announce_date", "report_period")
            if record.get(field) not in {None, ""}
        )
        keys = _keys(contract.dataset, records)
        unique_keys = len(set(keys))
        coverage = (len(set(expected) & set(present)) / len(expected)) if expected else 1.0
        results.append(
            FieldCoverageResult(
                dataset=contract.dataset,
                records=len(records),
                expected_fields=expected,
                present_fields=present,
                missing_fields=missing,
                null_counts=null_counts,
                null_ratios=null_ratios,
                duplicate_key_count=max(0, len(keys) - unique_keys),
                first_date=dates[0] if dates else None,
                last_date=dates[-1] if dates else None,
                ts_code_count=len({str(record.get("ts_code")) for record in records if record.get("ts_code")}),
                field_coverage_ratio=float(coverage),
            )
        )
    return results


def _keys(dataset: str, records: list[dict]) -> list[tuple]:
    key_fields = DATASET_PRIMARY_KEYS.get(dataset, ())
    if not key_fields:
        return []
    return [
        tuple(record.get(field) for field in key_fields)
        for record in records
        if all(record.get(field) not in {None, ""} for field in key_fields)
    ]
