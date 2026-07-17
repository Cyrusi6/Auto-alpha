from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
    ).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required:{path}")
    return payload


def publish_generation(
    root: str | Path,
    *,
    prefix: str,
    manifest_name: str,
    semantic: Mapping[str, Any],
    extra_files: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    output = Path(root)
    output.mkdir(parents=True, exist_ok=True)
    content_hash = canonical_hash(dict(semantic))
    generation_id = f"{prefix}_{content_hash[:24]}"
    manifest = dict(semantic) | {"content_hash": content_hash, "generation_id": generation_id}
    target = output / "generations" / generation_id
    files = {manifest_name: (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()}
    files.update(dict(extra_files or {}))
    if target.exists():
        for name, payload in files.items():
            path = target / name
            if not path.is_file() or path.read_bytes() != payload:
                raise ValueError(f"immutable_generation_collision:{generation_id}:{name}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{prefix}.", dir=target.parent))
        try:
            for name, payload in files.items():
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    atomic_json(
        output / "current.json",
        {
            "schema_version": f"{prefix}_pointer_v1",
            "content_hash": content_hash,
            "generation_id": generation_id,
            "manifest": f"generations/{generation_id}/{manifest_name}",
        },
    )
    return manifest | {"manifest_path": str(target / manifest_name)}


def validate_generation(path: str | Path, *, schema: str, manifest_name: str) -> dict[str, Any]:
    manifest_path = resolve_manifest(path, manifest_name)
    payload = read_json(manifest_path)
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if payload.get("schema_version") != schema or canonical_hash(semantic) != payload.get("content_hash"):
        raise ValueError(f"generation_schema_or_hash_invalid:{schema}")
    expected = str(payload.get("generation_id") or "")
    if not expected.endswith(str(payload["content_hash"])[:24]) or manifest_path.parent.name != expected:
        raise ValueError(f"generation_identity_invalid:{schema}")
    return payload | {"manifest_path": str(manifest_path)}


def resolve_manifest(path: str | Path, manifest_name: str) -> Path:
    candidate = Path(path)
    if candidate.is_file():
        return candidate.resolve()
    pointer = read_json(candidate / "current.json")
    relative = Path(str(pointer.get("manifest") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("pointer_manifest_invalid")
    resolved = (candidate / relative).resolve()
    if candidate.resolve() not in resolved.parents or resolved.name != manifest_name or not resolved.is_file():
        raise ValueError("pointer_manifest_missing_or_escape")
    return resolved


def atomic_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
