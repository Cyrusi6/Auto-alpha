"""Small immutable-artifact helpers for sealed-holdout validation; consolidated from auto_alpha.validation.walk_forward.red_team."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterable

from auto_alpha.platform.artifacts.schema.writer import attach_artifact_metadata


class HoldoutContractError(RuntimeError):
    """Raised when sealed-holdout evidence cannot be trusted."""


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def read_json(path: str | Path, *, artifact_type: str | None = None) -> dict[str, Any]:
    target = checked_regular_file(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HoldoutContractError(f"json_object_required:{target.name}")
    if artifact_type and payload.get("artifact_type") != artifact_type:
        raise HoldoutContractError(f"artifact_type_mismatch:{target.name}:{payload.get('artifact_type')}!={artifact_type}")
    return payload


def read_jsonl(path: str | Path, *, artifact_type: str | None = None) -> list[dict[str, Any]]:
    target = checked_regular_file(path)
    rows = []
    for line_number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise HoldoutContractError(f"jsonl_object_required:{target.name}:{line_number}")
        if artifact_type and row.get("artifact_type") not in {None, artifact_type}:
            raise HoldoutContractError(f"artifact_type_mismatch:{target.name}:{line_number}")
        rows.append(row)
    if artifact_type and rows and rows[0].get("artifact_type") is None:
        sidecar = read_json(f"{target}.schema.json")
        if sidecar.get("artifact_type") != artifact_type:
            raise HoldoutContractError(f"artifact_sidecar_type_mismatch:{target.name}")
        if int((sidecar.get("extra") or {}).get("record_count") or -1) != len(rows):
            raise HoldoutContractError(f"artifact_sidecar_record_count_mismatch:{target.name}")
    return rows


def checked_regular_file(path: str | Path) -> Path:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise HoldoutContractError(f"regular_file_required:{target}")
    return target.resolve()


def resolve_report_path(report_path: str | Path, raw_path: str | Path) -> Path:
    report = checked_regular_file(report_path)
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = report.parent / candidate
    candidate = checked_regular_file(candidate)
    if not candidate.is_relative_to(report.parent.resolve()):
        raise HoldoutContractError(f"campaign_artifact_outside_report_root:{candidate}")
    return candidate


def atomic_json(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp-{uuid.uuid4().hex}")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    _fsync_dir(target.parent)
    return target


def atomic_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp-{uuid.uuid4().hex}")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    _fsync_dir(target.parent)
    return target


def publish_generation(
    output_root: str | Path,
    *,
    generation_prefix: str,
    manifest_name: str,
    artifact_type: str,
    producer: str,
    core: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    root = Path(output_root).resolve()
    if root.is_symlink():
        raise HoldoutContractError("output_root_symlink_forbidden")
    generations = root / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    content_hash = stable_hash(core)
    generation_name = f"{generation_prefix}_{content_hash[:24]}"
    generation_dir = generations / generation_name
    payload = attach_artifact_metadata(
        {**core, "content_hash": content_hash, "generation_id": generation_name},
        artifact_type,
        producer,
    )
    if generation_dir.exists():
        existing = read_json(generation_dir / manifest_name, artifact_type=artifact_type)
        if existing.get("content_hash") != content_hash:
            raise HoldoutContractError("immutable_generation_conflict")
    else:
        temporary = generations / f".{generation_name}.tmp-{uuid.uuid4().hex}"
        temporary.mkdir(parents=True, exist_ok=False)
        atomic_json(temporary / manifest_name, payload)
        os.replace(temporary, generation_dir)
        _fsync_dir(generations)
    manifest_path = generation_dir / manifest_name
    pointer = {
        "generation": generation_name,
        "generation_path": str(Path("generations") / generation_name),
        "manifest": manifest_name,
        "manifest_sha256": sha256_file(manifest_path),
        "content_hash": content_hash,
    }
    atomic_json(root / f"current_{generation_prefix}.json", pointer)
    return manifest_path, read_json(manifest_path, artifact_type=artifact_type)


def remove_tree(path: str | Path) -> None:
    target = Path(path)
    if target.exists():
        shutil.rmtree(target)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
