"""Task 052-A immutable governed freeze generations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from artifact_schema.writer import write_json_artifact


DETERMINISTIC_CREATED_AT = "1970-01-01T00:00:00Z"
FREEZE_SEMANTIC_CONTRACT = {
    "task": "053-A",
    "copy_mode": "byte_exact",
    "publication": "content_addressed_atomic_directory_rename",
    "overwrite": "prohibited",
    "validation": "all_partition_hashes_recomputed",
    "manifest_contract": "governed_freeze_v2",
    "version": 2,
}

FREEZE_MANIFEST_FILENAMES = (
    "task_053a_governed_freeze_manifest.json",
    "task_052a_governed_freeze_manifest.json",
    "freeze_manifest.json",
)


class GovernedFreezeError(RuntimeError):
    """Raised when a governed freeze cannot be created or validated."""


@dataclass(frozen=True)
class GovernedFreezeResult:
    generation_id: str
    generation_dir: str
    manifest_path: str
    semantic_hash: str
    content_hash: str
    artifact_count: int


def create_task052_governed_freeze(
    artifacts: Mapping[str, str | Path],
    output_root: str | Path,
    *,
    source_lineage_manifest_path: str | Path,
) -> GovernedFreezeResult:
    """Copy governed inputs into one immutable, content-addressed generation."""

    if not artifacts:
        raise ValueError("artifacts must not be empty")
    lineage_path = Path(source_lineage_manifest_path)
    if not lineage_path.is_file():
        raise GovernedFreezeError(f"source lineage manifest missing: {lineage_path}")
    normalized: list[dict[str, Any]] = []
    seen_relative_paths: set[str] = set()
    for logical_name, raw_path in sorted(artifacts.items()):
        source = Path(raw_path)
        if not source.is_file():
            raise GovernedFreezeError(f"governed artifact missing: {source}")
        relative_path = _artifact_relative_path(str(logical_name), source)
        if relative_path in seen_relative_paths:
            raise GovernedFreezeError(f"duplicate governed freeze path: {relative_path}")
        seen_relative_paths.add(relative_path)
        normalized.append(
            {
                "logical_name": str(logical_name),
                "relative_path": relative_path,
                "sha256": _sha256(source),
                "size_bytes": source.stat().st_size,
                "source": source,
            }
        )
    semantic_hash = _hash_json(FREEZE_SEMANTIC_CONTRACT)
    lineage_hash = _sha256(lineage_path)
    content_inputs = {
        "semantic_hash": semantic_hash,
        "source_lineage_manifest_sha256": lineage_hash,
        "artifacts": [
            {key: item[key] for key in ("logical_name", "relative_path", "sha256", "size_bytes")}
            for item in normalized
        ],
    }
    content_hash = _hash_json(content_inputs)
    generation_id = f"freeze_053a_{content_hash[:24]}"
    target = Path(output_root) / generation_id
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        try:
            for item in normalized:
                destination = temporary / str(item["relative_path"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item["source"], destination)
                if _sha256(destination) != item["sha256"]:
                    raise GovernedFreezeError(f"copy hash mismatch: {item['logical_name']}")
                destination.chmod(0o444)
            lineage_destination = temporary / "lineage" / "source_lineage_manifest.json"
            lineage_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(lineage_path, lineage_destination)
            lineage_destination.chmod(0o444)
            manifest = {
                "generation_id": generation_id,
                "content_hash": content_hash,
                "semantic_hash": semantic_hash,
                "semantic_contract": FREEZE_SEMANTIC_CONTRACT,
                "source_lineage_manifest_sha256": lineage_hash,
                "artifacts": [
                    {key: item[key] for key in ("logical_name", "relative_path", "sha256", "size_bytes")}
                    for item in normalized
                ],
                "artifacts_by_name": {
                    str(item["logical_name"]): {
                        key: item[key] for key in ("relative_path", "sha256", "size_bytes")
                    }
                    for item in normalized
                },
                "datasets": {
                    str(item["logical_name"]): {
                        "records_path": str(item["relative_path"]),
                        "sha256": str(item["sha256"]),
                        "size_bytes": int(item["size_bytes"]),
                    }
                    for item in normalized
                },
                "immutable": True,
                "publication": "atomic_directory_rename",
            }
            manifest_path = write_json_artifact(
                temporary / "task_052a_governed_freeze_manifest.json",
                manifest,
                "task_052a_governed_freeze_manifest",
                "data_lake.task052_freeze",
                created_at=DETERMINISTIC_CREATED_AT,
            )
            manifest_path.chmod(0o444)
            compatibility_manifest = temporary / "freeze_manifest.json"
            compatibility_manifest.write_text(
                manifest_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            compatibility_manifest.chmod(0o444)
            os.replace(temporary, target)
        except Exception:
            _make_tree_writable(temporary)
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    validate_task052_governed_freeze(target, expected_content_hash=content_hash)
    return GovernedFreezeResult(
        generation_id=generation_id,
        generation_dir=str(target),
        manifest_path=str(target / "task_052a_governed_freeze_manifest.json"),
        semantic_hash=semantic_hash,
        content_hash=content_hash,
        artifact_count=len(normalized),
    )


def validate_task052_governed_freeze(
    generation_dir: str | Path,
    *,
    expected_content_hash: str | None = None,
) -> dict[str, Any]:
    root = Path(generation_dir)
    manifest_path = resolve_task052_governed_freeze_manifest(root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if expected_content_hash is not None and manifest.get("content_hash") != expected_content_hash:
        raise GovernedFreezeError("governed freeze content hash mismatch")
    semantic_contract = manifest.get("semantic_contract")
    if not isinstance(semantic_contract, dict):
        raise GovernedFreezeError("governed freeze semantic contract missing")
    if manifest.get("semantic_hash") != _hash_json(semantic_contract):
        raise GovernedFreezeError("governed freeze semantic hash mismatch")
    checked = 0
    for artifact in manifest.get("artifacts", []):
        path = root / str(artifact["relative_path"])
        if not path.is_file():
            raise GovernedFreezeError(f"frozen artifact missing: {artifact['relative_path']}")
        if _sha256(path) != artifact.get("sha256"):
            raise GovernedFreezeError(f"frozen artifact drift: {artifact['relative_path']}")
        checked += 1
    lineage_path = root / "lineage" / "source_lineage_manifest.json"
    if not lineage_path.is_file() or _sha256(lineage_path) != manifest.get("source_lineage_manifest_sha256"):
        raise GovernedFreezeError("frozen source lineage drift")
    return {
        "valid": True,
        "generation_id": manifest.get("generation_id"),
        "content_hash": manifest.get("content_hash"),
        "semantic_hash": manifest.get("semantic_hash"),
        "checked_artifacts": checked,
        "manifest_path": str(manifest_path),
        "artifacts_by_name": {
            str(item["logical_name"]): str(item["relative_path"])
            for item in manifest.get("artifacts", [])
        },
    }


def resolve_task052_governed_freeze_manifest(generation_dir: str | Path) -> Path:
    root = Path(generation_dir)
    for filename in FREEZE_MANIFEST_FILENAMES:
        candidate = root / filename
        if candidate.is_file():
            return candidate
    raise GovernedFreezeError(
        f"governed freeze manifest missing under {root}; expected one of {','.join(FREEZE_MANIFEST_FILENAMES)}"
    )


def _artifact_relative_path(logical_name: str, source: Path) -> str:
    safe_name = logical_name.replace("\\", "/").strip("/")
    if not safe_name or safe_name.startswith(".") or ".." in Path(safe_name).parts:
        raise GovernedFreezeError(f"invalid logical artifact name: {logical_name}")
    if "." in Path(safe_name).name:
        return f"artifacts/{safe_name}"
    return f"artifacts/{safe_name}/{source.name}"


def _make_tree_writable(root: Path) -> None:
    if not root.exists():
        return
    for path in root.rglob("*"):
        try:
            path.chmod(0o755 if path.is_dir() else 0o644)
        except OSError:
            pass


def _hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
