"""Schema-aware JSON and JSONL artifact writers."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .models import ArtifactMetadata
from .registry import get_definition


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def attach_artifact_metadata(
    payload: dict[str, Any],
    artifact_type: str,
    producer: str,
    schema_version: str | None = None,
    created_at: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definition = get_definition(artifact_type)
    version = schema_version or (definition.schema_version if definition else "1.0")
    timestamp = created_at or payload.get("created_at") or utc_now()
    metadata = ArtifactMetadata(
        artifact_type=artifact_type,
        schema_version=version,
        producer=producer,
        created_at=str(timestamp),
        extra=extra or {},
    ).to_dict()
    return {
        **payload,
        "artifact_type": artifact_type,
        "schema_version": version,
        "producer": producer,
        "created_at": payload.get("created_at", timestamp),
        "artifact_metadata": metadata,
    }


def write_json_artifact(
    path: str | Path,
    payload: dict[str, Any],
    artifact_type: str,
    producer: str,
    schema_version: str | None = None,
    created_at: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    enriched = attach_artifact_metadata(payload, artifact_type, producer, schema_version=schema_version, created_at=created_at, extra=extra)
    target.write_text(json.dumps(enriched, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return target


def write_jsonl_artifact(
    path: str | Path,
    records: Iterable[Any],
    artifact_type: str,
    producer: str,
    schema_version: str | None = None,
    created_at: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            payload = _to_jsonable(record)
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            count += 1
    write_artifact_sidecar(
        target,
        ArtifactMetadata(
            artifact_type=artifact_type,
            schema_version=schema_version or (get_definition(artifact_type).schema_version if get_definition(artifact_type) else "1.0"),
            producer=producer,
            created_at=created_at or utc_now(),
            extra={**(extra or {}), "record_count": count},
        ),
    )
    return target


def write_artifact_sidecar(path: str | Path, metadata: ArtifactMetadata | dict[str, Any]) -> Path:
    target = Path(f"{path}.schema.json")
    payload = metadata.to_dict() if hasattr(metadata, "to_dict") else dict(metadata)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return target


def _to_jsonable(record: Any) -> dict[str, Any]:
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)
    if hasattr(record, "to_dict"):
        return dict(record.to_dict())
    if isinstance(record, dict):
        return dict(record)
    raise TypeError(f"unsupported artifact record: {type(record)!r}")
