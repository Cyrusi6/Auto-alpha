"""Streaming raw JSONL sidecar index builder."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from artifact_schema.writer import utc_now

from .models import RawDataIndexManifest, RawDataIndexStatus, RawDatasetIndex, RawPartitionRecord
from .partitioner import PartitionBuilder
from .registry import (
    DATE_FIELD_CANDIDATES,
    default_datasets,
    primary_date_field,
    primary_key_fields,
)


def build_raw_data_index(
    data_dir: str | Path,
    *,
    datasets: Sequence[str] | None = None,
    output_dir: str | Path | None = None,
    profile_name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    partition_granularity: str = "monthly",
    max_records: int | None = None,
    run_dir: str | Path | None = None,
    allow_active_run_index: bool = False,
    recent_mtime_minutes: int = 15,
) -> tuple[RawDataIndexManifest | None, list[RawDatasetIndex], list[RawPartitionRecord], list[dict[str, Any]], dict[str, Any]]:
    """Build index objects without writing artifacts.

    The function is intentionally conservative about active downloads. If a
    running backfill state or very recent records file is detected, callers get
    a blocked safety summary and no manifest unless explicitly allowed.
    """

    selected = list(datasets or default_datasets())
    root = Path(data_dir)
    safety = active_run_safety_check(
        data_dir=root,
        run_dir=run_dir,
        selected_datasets=selected,
        recent_mtime_minutes=recent_mtime_minutes,
        allow_active_run_index=allow_active_run_index,
    )
    issues: list[dict[str, Any]] = []
    if safety.get("blocked") and not allow_active_run_index:
        issues.extend(safety.get("issues", []))
        return None, [], [], issues, safety

    indexes: list[RawDatasetIndex] = []
    partitions: list[RawPartitionRecord] = []
    for dataset in selected:
        index, dataset_partitions, dataset_issues = scan_dataset(
            root,
            dataset,
            partition_granularity=partition_granularity,
            max_records=max_records,
        )
        indexes.append(index)
        partitions.extend(dataset_partitions)
        issues.extend(dataset_issues)

    built_at = utc_now()
    manifest_hash = _manifest_hash(indexes, partitions, partition_granularity)
    index_id = f"raw_index_{manifest_hash[:16]}"
    status = _manifest_status(indexes, issues)
    dataset_indexes_path = str(Path(output_dir or root) / "raw_dataset_indexes.jsonl")
    partitions_path = str(Path(output_dir or root) / "raw_partitions.jsonl")
    issues_path = str(Path(output_dir or root) / "raw_data_index_issues.jsonl")
    manifest = RawDataIndexManifest(
        index_id=index_id,
        status=status,
        data_dir=str(root),
        profile_name=profile_name,
        start_date=start_date,
        end_date=end_date,
        partition_granularity=partition_granularity,
        dataset_count=len(indexes),
        total_records=sum(item.record_count for item in indexes),
        total_size_bytes=sum(item.file_size_bytes for item in indexes),
        total_parse_errors=sum(item.parse_error_count for item in indexes),
        total_duplicate_key_estimate=sum(item.duplicate_key_count_estimate for item in indexes),
        partition_count=len(partitions),
        index_hash=manifest_hash,
        dataset_indexes_path=dataset_indexes_path,
        partitions_path=partitions_path,
        issues_path=issues_path,
        built_at=built_at,
        datasets=[item.to_dict() for item in indexes],
        source_summary={
            "datasets": selected,
            "max_records": max_records,
            "truncated": bool(max_records),
        },
        safety=safety,
    )
    return manifest, indexes, partitions, issues, safety


def scan_dataset(
    data_dir: str | Path,
    dataset: str,
    *,
    partition_granularity: str = "monthly",
    max_records: int | None = None,
    duplicate_sample_limit: int = 1_000_000,
    null_field_limit: int = 200,
) -> tuple[RawDatasetIndex, list[RawPartitionRecord], list[dict[str, Any]]]:
    path = Path(data_dir) / dataset / "records.jsonl"
    now = utc_now()
    key_fields = primary_key_fields(dataset)
    if not path.exists():
        issue = _issue("warning", "missing_records", f"records.jsonl missing for {dataset}", dataset, str(path))
        return (
            RawDatasetIndex(
                dataset=dataset,
                records_path=str(path),
                records_sha256="",
                file_size_bytes=0,
                record_count=0,
                parse_error_count=0,
                first_date=None,
                last_date=None,
                ts_code_count=0,
                index_code_count=0,
                ann_date_first=None,
                ann_date_last=None,
                end_date_first=None,
                end_date_last=None,
                primary_key_fields=key_fields,
                duplicate_key_count_estimate=0,
                null_field_summary={},
                partition_count=0,
                built_at=now,
                source_mtime=0.0,
                status=RawDataIndexStatus.missing,
                warning_count=1,
                warnings=["records.jsonl missing"],
            ),
            [],
            [issue],
        )

    digest = hashlib.sha256()
    record_count = 0
    parse_error_count = 0
    duplicate_keys = 0
    key_tracking_limited = False
    seen_keys: set[tuple[str, ...]] = set()
    ts_codes: set[str] = set()
    index_codes: set[str] = set()
    nulls: Counter[str] = Counter()
    dates: dict[str, list[str | None]] = {field: [None, None] for field in DATE_FIELD_CANDIDATES}
    parse_samples: list[dict[str, Any]] = []
    partitioner = PartitionBuilder(dataset, granularity=partition_granularity)
    issues: list[dict[str, Any]] = []
    offset = 0
    truncated = False
    with path.open("rb") as handle:
        for raw_line in handle:
            start_offset = offset
            offset += len(raw_line)
            if not raw_line.strip():
                digest.update(raw_line)
                continue
            if max_records is not None and record_count >= max_records:
                truncated = True
                break
            digest.update(raw_line)
            try:
                payload = json.loads(raw_line.decode("utf-8"))
            except Exception as exc:
                parse_error_count += 1
                if len(parse_samples) < 5:
                    parse_samples.append({"line": record_count + parse_error_count, "error": str(exc)[:200]})
                continue
            if not isinstance(payload, dict):
                parse_error_count += 1
                if len(parse_samples) < 5:
                    parse_samples.append({"line": record_count + parse_error_count, "error": "record is not a JSON object"})
                continue
            record_count += 1
            ts_code = payload.get("ts_code") or payload.get("con_code")
            if ts_code:
                ts_codes.add(str(ts_code))
            index_code = payload.get("index_code") or payload.get("l1_code")
            if index_code:
                index_codes.add(str(index_code))
            for field, value in payload.items():
                if (value is None or value == "") and len(nulls) < null_field_limit:
                    nulls[str(field)] += 1
            for field in DATE_FIELD_CANDIDATES:
                value = payload.get(field)
                if value:
                    _update_range(dates[field], str(value))
            if key_fields and not key_tracking_limited:
                key = tuple(str(payload.get(field, "")) for field in key_fields)
                if key in seen_keys:
                    duplicate_keys += 1
                elif len(seen_keys) < duplicate_sample_limit:
                    seen_keys.add(key)
                else:
                    key_tracking_limited = True
            partitioner.add(payload, start_offset, offset)

    warnings: list[str] = []
    if parse_error_count:
        warnings.append(f"parse errors: {parse_error_count}")
        issues.append(_issue("warning", "parse_errors", f"{dataset} has parse errors", dataset, str(path), {"parse_error_count": parse_error_count, "samples": parse_samples}))
    if duplicate_keys:
        warnings.append(f"duplicate primary keys: {duplicate_keys}")
        issues.append(_issue("warning", "duplicate_keys", f"{dataset} has duplicate primary keys", dataset, str(path), {"duplicate_key_count_estimate": duplicate_keys}))
    if key_tracking_limited:
        warnings.append("duplicate key tracking reached sample limit")
    if truncated:
        warnings.append(f"index scan truncated at max_records={max_records}")
    if record_count == 0 and parse_error_count == 0:
        warnings.append("empty records file")
    primary_range = dates.get(primary_date_field(dataset) or "", [None, None])
    if primary_range == [None, None]:
        primary_range = _best_date_range(dates)
    partitions = partitioner.records()
    status = RawDataIndexStatus.partial if truncated or record_count == 0 else RawDataIndexStatus.fresh
    if parse_error_count:
        status = RawDataIndexStatus.partial
    return (
        RawDatasetIndex(
            dataset=dataset,
            records_path=str(path),
            records_sha256=digest.hexdigest(),
            file_size_bytes=path.stat().st_size,
            record_count=record_count,
            parse_error_count=parse_error_count,
            first_date=primary_range[0],
            last_date=primary_range[1],
            ts_code_count=len(ts_codes),
            index_code_count=len(index_codes),
            ann_date_first=dates["ann_date"][0],
            ann_date_last=dates["ann_date"][1],
            end_date_first=dates["end_date"][0],
            end_date_last=dates["end_date"][1],
            primary_key_fields=key_fields,
            duplicate_key_count_estimate=duplicate_keys,
            null_field_summary=dict(nulls),
            partition_count=len(partitions),
            built_at=now,
            source_mtime=path.stat().st_mtime,
            status=status,
            warning_count=len(warnings),
            warnings=warnings,
            metadata={
                "primary_date_field": primary_date_field(dataset),
                "truncated": truncated,
                "key_tracking_limited": key_tracking_limited,
            },
        ),
        partitions,
        issues,
    )


def active_run_safety_check(
    *,
    data_dir: Path,
    run_dir: str | Path | None,
    selected_datasets: Sequence[str],
    recent_mtime_minutes: int = 15,
    allow_active_run_index: bool = False,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    now = time.time()
    recent_threshold = max(0, recent_mtime_minutes) * 60
    state_path = Path(run_dir) / "backfill_state.json" if run_dir else None
    if state_path and state_path.exists():
        age = now - state_path.stat().st_mtime
        if recent_threshold and age <= recent_threshold:
            issues.append(_issue("error", "active_backfill_state", "backfill_state.json changed recently; active run index is blocked", None, str(state_path), {"age_seconds": round(age, 3)}))
    recent_records: list[dict[str, Any]] = []
    for dataset in selected_datasets:
        path = data_dir / dataset / "records.jsonl"
        if not path.exists():
            continue
        age = now - path.stat().st_mtime
        if recent_threshold and age <= recent_threshold:
            recent_records.append({"dataset": dataset, "path": str(path), "age_seconds": round(age, 3)})
    if recent_records:
        issues.append(_issue("warning", "recent_records_mtime", "one or more records.jsonl files changed recently", None, str(data_dir), {"recent_records": recent_records[:20], "recent_record_count": len(recent_records)}))
    blocked = any(item["severity"] == "error" for item in issues) and not allow_active_run_index
    return {
        "blocked": blocked,
        "allow_active_run_index": bool(allow_active_run_index),
        "recent_mtime_minutes": recent_mtime_minutes,
        "state_path": str(state_path) if state_path else None,
        "recent_record_count": len(recent_records),
        "issues": issues,
    }


def _update_range(slot: list[str | None], value: str) -> None:
    slot[0] = value if slot[0] is None or value < slot[0] else slot[0]
    slot[1] = value if slot[1] is None or value > slot[1] else slot[1]


def _best_date_range(dates: dict[str, list[str | None]]) -> list[str | None]:
    for field in ("trade_date", "ann_date", "end_date", "list_date", "start_date"):
        value = dates.get(field)
        if value and value != [None, None]:
            return value
    return [None, None]


def _manifest_hash(indexes: list[RawDatasetIndex], partitions: list[RawPartitionRecord], granularity: str) -> str:
    payload = {
        "granularity": granularity,
        "datasets": [
            {
                "dataset": item.dataset,
                "sha256": item.records_sha256,
                "records": item.record_count,
                "size": item.file_size_bytes,
                "status": item.status,
            }
            for item in sorted(indexes, key=lambda item: item.dataset)
        ],
        "partitions": len(partitions),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _manifest_status(indexes: list[RawDatasetIndex], issues: list[dict[str, Any]]) -> str:
    if any(item.status == RawDataIndexStatus.failed for item in indexes) or any(item.get("severity") == "error" for item in issues):
        return RawDataIndexStatus.failed
    if any(item.status in {RawDataIndexStatus.missing, RawDataIndexStatus.partial} for item in indexes):
        return RawDataIndexStatus.partial
    return RawDataIndexStatus.fresh


def _issue(severity: str, code: str, message: str, dataset: str | None, path: str | None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "dataset": dataset,
        "path": path,
        "metadata": dict(metadata or {}),
    }
