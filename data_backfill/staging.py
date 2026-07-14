"""Backfill staging, quarantine, and state helpers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from artifact_schema.writer import utc_now, write_json_artifact, write_jsonl_artifact
from data_pipeline.ashare.dataset_registry import DATASET_DEFINITIONS, DATASET_PRIMARY_KEYS

from .models import BackfillJob, BackfillJobStatus, BackfillRunState


def load_backfill_state(path: str | Path, plan_id: str = "") -> BackfillRunState:
    target = Path(path)
    if not target.exists():
        return BackfillRunState(plan_id=plan_id, updated_at=utc_now(), jobs={})
    payload = json.loads(target.read_text(encoding="utf-8"))
    return BackfillRunState(
        plan_id=str(payload.get("plan_id") or plan_id),
        updated_at=str(payload.get("updated_at") or ""),
        jobs=dict(payload.get("jobs") or {}),
    )


def save_backfill_state(state: BackfillRunState, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json_artifact(target, state.to_dict(), "backfill_state", "data_backfill")
    return target


def mark_job(state: BackfillRunState, job: BackfillJob) -> BackfillRunState:
    jobs = dict(state.jobs)
    jobs[job.job_id] = job.to_dict()
    return BackfillRunState(plan_id=state.plan_id, updated_at=utc_now(), jobs=jobs)


def successful_job_ids(state: BackfillRunState) -> set[str]:
    return {job_id for job_id, payload in state.jobs.items() if payload.get("status") == BackfillJobStatus.success}


def write_staging_records(root: str | Path, job: BackfillJob, records: Iterable[Any]) -> tuple[Path, Path, int]:
    job_dir = Path(root) / "jobs" / job.job_id
    dataset_dir = job_dir / job.dataset
    payloads = [_to_jsonable(record) for record in records]
    records_path = write_jsonl_artifact(dataset_dir / "records.jsonl", payloads, "backfill_staging_records", "data_backfill")
    result_path = write_json_artifact(
        job_dir / "job_result.json",
        {**job.to_dict(), "records": len(payloads), "staging_path": str(records_path)},
        "backfill_job_result",
        "data_backfill",
    )
    return records_path, result_path, len(payloads)


def write_negative_attestation(
    root: str | Path,
    job: BackfillJob,
    attestations: list[dict[str, Any]],
) -> Path:
    if not attestations:
        attestations = [{"assertion": "provider_returned_zero_rows", "item_count": 0}]
    payload = {
        "job_id": job.job_id,
        "dataset": job.dataset,
        "provider": job.provider,
        "assertion": "no_rows_for_normalized_requests",
        "attestations": attestations,
        "created_at": utc_now(),
    }
    return write_json_artifact(
        Path(root) / "jobs" / job.job_id / "negative_attestation.json",
        payload,
        "backfill_negative_attestation",
        "data_backfill",
    )


def atomic_publish_staging(
    data_dir: str | Path,
    staging_records_path: str | Path,
    job: BackfillJob,
) -> dict[str, Any]:
    """Validate, merge, fsync, and atomically publish one staged job."""

    staging_path = Path(staging_records_path)
    staged = read_jsonl_strict(staging_path, dataset=job.dataset)
    target = Path(data_dir) / job.dataset / "records.jsonl"
    existing = read_jsonl_strict(target, dataset=job.dataset) if target.exists() else []
    merged, written, dedup = _merge_records(job.dataset, existing, staged)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".records.", suffix=".staging", dir=target.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for payload in merged:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        published_hash = _sha256(temporary_path)
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)
    receipt = {
        "job_id": job.job_id,
        "dataset": job.dataset,
        "staging_path": str(staging_path),
        "staging_sha256": _sha256(staging_path),
        "output_path": str(target),
        "output_sha256_at_publish": published_hash,
        "fetched": len(staged),
        "written": written,
        "dedup": dedup,
        "dataset_total": len(merged),
        "published_at": utc_now(),
        "atomic_publish": True,
    }
    receipt_path = write_json_artifact(
        staging_path.parents[1] / "publish_receipt.json",
        receipt,
        "backfill_publish_receipt",
        "data_backfill",
    )
    return {**receipt, "publish_receipt_path": str(receipt_path)}


def read_jsonl_strict(path: str | Path, dataset: str | None = None) -> list[dict[str, Any]]:
    target = Path(path)
    raw = target.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError(f"truncated JSONL file (missing final newline): {target}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"blank JSONL row at {target}:{line_number}")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"corrupt JSONL row at {target}:{line_number}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"JSONL row must be an object at {target}:{line_number}")
        if dataset is not None:
            _validate_record_schema(dataset, payload, target, line_number)
        rows.append(payload)
    return rows


def resume_evidence_valid(payload: dict[str, Any]) -> bool:
    output_path = Path(str(payload.get("output_path") or ""))
    receipt_path = Path(str(payload.get("publish_receipt_path") or ""))
    if not output_path.is_file() or not receipt_path.is_file():
        return False
    if int(payload.get("fetched", 0)) == 0:
        negative_path = Path(str(payload.get("negative_attestation_path") or ""))
        if not negative_path.is_file():
            return False
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(receipt.get("atomic_publish")) and receipt.get("job_id") == payload.get("job_id")


def quarantine_job(staging_root: str | Path, quarantine_root: str | Path, job_id: str) -> Path | None:
    source = Path(staging_root) / "jobs" / job_id
    if not source.exists():
        return None
    target = Path(quarantine_root) / "jobs" / job_id
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return target


def _to_jsonable(record: Any) -> dict[str, Any]:
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)
    if hasattr(record, "to_dict"):
        return dict(record.to_dict())
    if isinstance(record, dict):
        return dict(record)
    raise TypeError(f"unsupported backfill record: {type(record)!r}")


def _merge_records(dataset: str, existing: list[dict[str, Any]], staged: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    key_fields = DATASET_PRIMARY_KEYS.get(dataset)
    if not key_fields:
        return [*existing, *staged], len(staged), 0
    merged = list(existing)
    keys = {_record_key(record, key_fields) for record in existing}
    written = 0
    dedup = 0
    for record in staged:
        key = _record_key(record, key_fields)
        if key in keys:
            dedup += 1
            continue
        keys.add(key)
        merged.append(record)
        written += 1
    return merged, written, dedup


def _record_key(record: dict[str, Any], fields: tuple[str, ...]) -> tuple[Any, ...]:
    key = tuple(record.get(field) for field in fields)
    if any(value in {None, ""} for value in key):
        raise ValueError(f"record missing primary key fields: {fields}")
    return key


def _validate_record_schema(dataset: str, payload: dict[str, Any], path: Path, line_number: int) -> None:
    definition = DATASET_DEFINITIONS.get(dataset)
    if definition is None:
        return
    missing = [field for field in definition.fields if field not in payload]
    if missing:
        raise ValueError(f"schema mismatch at {path}:{line_number}; missing fields: {','.join(missing)}")
    if dataset == "suspensions" and payload.get("suspend_type") not in {"S", "R"}:
        raise ValueError(f"invalid suspend_type at {path}:{line_number}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
