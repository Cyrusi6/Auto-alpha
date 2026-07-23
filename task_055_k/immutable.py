from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from task_055_h.io import canonical_hash, read_json


class Task055KImmutableError(RuntimeError):
    pass


def write_immutable_generation(
    root: str | Path,
    *,
    prefix: str,
    manifest_name: str,
    semantic: Mapping[str, Any],
    extra_files: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    output = Path(root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    content_hash = canonical_hash(dict(semantic))
    generation_id = f"{prefix}_{content_hash[:24]}"
    manifest = dict(semantic) | {"content_hash": content_hash, "generation_id": generation_id}
    files = {manifest_name: _json_bytes(manifest)}
    files.update(dict(extra_files or {}))
    target = output / "generations" / generation_id
    if target.exists():
        _validate_exact_files(target, files)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{prefix}.", dir=target.parent))
        try:
            for relative, payload in files.items():
                path = staging / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            try:
                os.rename(staging, target)
                _fsync_dir(target.parent)
            except OSError:
                if not target.exists():
                    raise
                _validate_exact_files(target, files)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return manifest | {"manifest_path": str(target / manifest_name), "root": str(output)}


def publish_current_pointer(
    root: str | Path,
    *,
    manifest: Mapping[str, Any],
    manifest_name: str,
    pointer_schema: str,
) -> Path:
    output = Path(root).resolve()
    manifest_path = Path(str(manifest.get("manifest_path") or "")).resolve()
    if output not in manifest_path.parents or manifest_path.name != manifest_name:
        raise Task055KImmutableError("task055k_pointer_manifest_escape")
    relative = manifest_path.relative_to(output).as_posix()
    payload = {
        "schema_version": pointer_schema,
        "content_hash": manifest["content_hash"],
        "generation_id": manifest["generation_id"],
        "manifest": relative,
    }
    pointer = output / "current.json"
    if pointer.is_symlink():
        raise Task055KImmutableError("task055k_current_pointer_symlink")
    if pointer.is_file():
        existing = read_json(pointer)
        if existing != payload:
            raise Task055KImmutableError("task055k_current_pointer_replacement_forbidden")
        return pointer
    _link_once_json(pointer, payload)
    existing = read_json(pointer)
    if existing != payload:
        raise Task055KImmutableError("task055k_current_pointer_concurrent_conflict")
    _fsync_dir(output)
    return pointer


def validate_current_pointer(
    root: str | Path,
    *,
    manifest_name: str,
    pointer_schema: str,
) -> Path:
    output = Path(root).resolve()
    pointer = output / "current.json"
    if not pointer.is_file() or pointer.is_symlink():
        raise Task055KImmutableError("task055k_current_pointer_missing_or_symlink")
    payload = read_json(pointer)
    if payload.get("schema_version") != pointer_schema:
        raise Task055KImmutableError("task055k_current_pointer_schema_invalid")
    relative = Path(str(payload.get("manifest") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise Task055KImmutableError("task055k_current_pointer_path_invalid")
    manifest = (output / relative).resolve()
    if output not in manifest.parents or manifest.name != manifest_name or not manifest.is_file():
        raise Task055KImmutableError("task055k_current_pointer_target_invalid")
    row = read_json(manifest)
    if row.get("content_hash") != payload.get("content_hash") or row.get("generation_id") != payload.get("generation_id"):
        raise Task055KImmutableError("task055k_current_pointer_lineage_invalid")
    return manifest


def _validate_exact_files(target: Path, files: Mapping[str, bytes]) -> None:
    if target.is_symlink() or not target.is_dir():
        raise Task055KImmutableError("task055k_immutable_generation_root_invalid")
    if any(path.is_symlink() for path in target.rglob("*")):
        raise Task055KImmutableError("task055k_immutable_generation_symlink_forbidden")
    actual = sorted(path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file())
    expected = sorted(files)
    if actual != expected:
        raise Task055KImmutableError("task055k_immutable_generation_file_set_collision")
    for relative, payload in files.items():
        if (target / relative).read_bytes() != payload:
            raise Task055KImmutableError(f"task055k_immutable_generation_content_collision:{relative}")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(payload), indent=2, sort_keys=True) + "\n").encode()


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _link_once_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return
        _fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
