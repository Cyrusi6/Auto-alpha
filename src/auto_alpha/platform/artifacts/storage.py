"""Canonical content-addressed artifact storage and hashing primitives."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping


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


def publish_prepared_generation(
    root: str | Path,
    *,
    prepared_directory: str | Path,
    manifest_name: str,
    validator: Callable[[Path], Mapping[str, Any]],
    pointer_schema: str,
    pointer_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically publish a complete prepared directory as one immutable generation.

    The prepared directory must already contain its final content-addressed
    manifest and its basename must equal the manifest's ``generation_id``. The
    publisher freezes and validates it before the atomic rename. A per-identity
    process lock makes concurrent publication idempotent, while an already
    published generation is reused only when its exact directory/file closure
    and every file byte match the prepared candidate.
    """

    lexical_output = Path(root)
    if _has_lexical_symlink_component(lexical_output):
        raise ValueError("output_root_symlink_forbidden")
    output = lexical_output.resolve()
    lexical_prepared = Path(prepared_directory)
    if _has_lexical_symlink_component(lexical_prepared):
        raise ValueError("prepared_generation_directory_symlink_forbidden")
    prepared = lexical_prepared.resolve()
    manifest_relative = Path(manifest_name)
    if (
        not manifest_name
        or manifest_relative.is_absolute()
        or len(manifest_relative.parts) != 1
        or manifest_relative.name != manifest_name
    ):
        raise ValueError("prepared_generation_manifest_name_invalid")
    if not prepared.is_dir():
        raise ValueError("prepared_generation_directory_invalid")
    manifest_path = prepared / manifest_name
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("prepared_generation_manifest_missing")
    manifest = read_json(manifest_path)
    content_hash = str(manifest.get("content_hash") or "")
    generation_id = str(manifest.get("generation_id") or "")
    if (
        len(content_hash) != 64
        or any(character not in "0123456789abcdef" for character in content_hash)
        or not generation_id
        or not generation_id.endswith(content_hash[:24])
        or prepared.name != generation_id
    ):
        raise ValueError("prepared_generation_identity_invalid")
    final_target = output / "generations" / generation_id
    if (
        prepared == final_target
        or prepared == output
        or prepared in output.parents
    ):
        raise ValueError("prepared_generation_output_overlap")
    if not isinstance(pointer_schema, str) or not pointer_schema.strip():
        raise ValueError("prepared_generation_pointer_schema_invalid")
    reserved_pointer_fields = {
        "schema_version",
        "content_hash",
        "generation_id",
        "manifest",
    }
    extra_pointer_fields = dict(pointer_fields or {})
    if reserved_pointer_fields & set(extra_pointer_fields):
        raise ValueError("prepared_generation_pointer_fields_invalid")

    prepared_closure = _tree_closure(prepared)
    _freeze_tree(prepared)
    _assert_tree_immutable(prepared)
    validated_prepared = dict(validator(manifest_path))
    _assert_validated_identity(
        validated_prepared,
        generation_id=generation_id,
        content_hash=content_hash,
    )
    if _tree_closure(prepared) != prepared_closure:
        raise ValueError("prepared_generation_changed_during_validation")

    generations = output / "generations"
    if _has_lexical_symlink_component(generations):
        raise ValueError("generation_namespace_symlink_forbidden")
    generations.mkdir(parents=True, exist_ok=True)
    target = final_target
    cache_hit = False
    with _generation_publish_lock(generations, generation_id):
        _assert_tree_immutable(prepared)
        if _tree_closure(prepared) != prepared_closure:
            raise ValueError("prepared_generation_changed_before_publish")
        if target.exists() or target.is_symlink():
            if not target.is_dir() or target.is_symlink():
                raise ValueError(f"immutable_generation_collision:{generation_id}:target")
            mismatch = _tree_mismatch(prepared, target)
            if mismatch is not None:
                raise ValueError(
                    f"immutable_generation_collision:{generation_id}:{mismatch}"
                )
            # A process may have stopped after the namespace rename but before
            # re-closing the generation root. Exact closure is checked first so
            # recovery never blesses bytes created in that narrow window.
            _freeze_tree(target)
            _assert_tree_immutable(target)
            validated_target = dict(validator(target / manifest_name))
            _assert_validated_identity(
                validated_target,
                generation_id=generation_id,
                content_hash=content_hash,
            )
            _remove_prepared_tree(prepared)
            cache_hit = True
        else:
            # Some controlled filesystems require write permission on the
            # directory inode being moved. Payload files and child directories
            # remain immutable; only the root is opened for the namespace move.
            prepared.chmod(0o750)
            try:
                os.rename(prepared, target)
            except BaseException:
                if prepared.exists():
                    prepared.chmod(0o550)
                raise
            target.chmod(0o550)
            _assert_tree_immutable(target)
            if _tree_closure(target) != prepared_closure:
                raise ValueError("prepared_generation_changed_during_publish")
            _fsync_directory(generations)
            validated_target = dict(validator(target / manifest_name))
            _assert_validated_identity(
                validated_target,
                generation_id=generation_id,
                content_hash=content_hash,
            )

        pointer = {
            "schema_version": pointer_schema,
            "content_hash": content_hash,
            "generation_id": generation_id,
            "manifest": f"generations/{generation_id}/{manifest_name}",
            **extra_pointer_fields,
        }
        atomic_json(output / "current.json", pointer)
        _fsync_directory(output)
    return validated_target | {
        "manifest_path": str(target / manifest_name),
        "cache_hit": cache_hit,
    }


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


def _has_lexical_symlink_component(path: Path) -> bool:
    absolute = path if path.is_absolute() else Path.cwd() / path
    return any(component.is_symlink() for component in (absolute, *absolute.parents))


def _tree_closure(root: Path) -> tuple[tuple[str, str, int, str], ...]:
    rows: list[tuple[str, str, int, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"prepared_generation_symlink_forbidden:{relative}")
        if path.is_dir():
            rows.append((relative, "directory", 0, ""))
        elif path.is_file():
            rows.append((relative, "file", path.stat().st_size, sha256_file(path)))
        else:
            raise ValueError(f"prepared_generation_special_file_forbidden:{relative}")
    return tuple(rows)


def _freeze_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o550 if path.is_dir() else 0o440)
    root.chmod(0o550)


def _assert_tree_immutable(root: Path) -> None:
    if root.stat().st_mode & 0o222:
        raise ValueError("prepared_generation_root_mutable")
    for path in root.rglob("*"):
        if path.is_symlink() or path.stat().st_mode & 0o222:
            raise ValueError(
                f"prepared_generation_entry_mutable_or_symlink:{path.relative_to(root)}"
            )


def _tree_mismatch(left: Path, right: Path) -> str | None:
    left_closure = _tree_closure(left)
    right_closure = _tree_closure(right)
    left_shape = tuple((path, kind) for path, kind, _size, _digest in left_closure)
    right_shape = tuple((path, kind) for path, kind, _size, _digest in right_closure)
    if left_shape != right_shape:
        return "file_closure"
    for relative, kind, size, digest in left_closure:
        if kind != "file":
            continue
        counterpart = right / relative
        if counterpart.stat().st_size != size or sha256_file(counterpart) != digest:
            return relative
        if not _files_equal(left / relative, counterpart):
            return relative
    return None


def _files_equal(left: Path, right: Path) -> bool:
    with left.open("rb") as left_handle, right.open("rb") as right_handle:
        while True:
            left_chunk = left_handle.read(1024 * 1024)
            right_chunk = right_handle.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _assert_validated_identity(
    payload: Mapping[str, Any],
    *,
    generation_id: str,
    content_hash: str,
) -> None:
    if (
        payload.get("generation_id") != generation_id
        or payload.get("content_hash") != content_hash
    ):
        raise ValueError("prepared_generation_validator_identity_mismatch")


@contextmanager
def _generation_publish_lock(generations: Path, generation_id: str) -> Iterator[None]:
    lock_root = generations.parent / ".publish_locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{generation_id}.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _remove_prepared_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o750)
    root.chmod(0o750)
    shutil.rmtree(root)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
