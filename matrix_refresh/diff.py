"""Source hash diffing for matrix caches."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import MatrixSourceDiff


def compute_source_hash(data_dir: str | Path, data_version_manifest_path: str | Path | None = None) -> str | None:
    manifest = Path(data_version_manifest_path) if data_version_manifest_path else None
    if manifest is not None and manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            content_hash = payload.get("content_hash")
            if content_hash:
                return str(content_hash)
        except json.JSONDecodeError:
            pass
    root = Path(data_dir)
    if not root.exists():
        return None
    digest = hashlib.sha256()
    for path in sorted(root.glob("*/records.jsonl")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def matrix_metadata_hash(matrix_cache_dir: str | Path) -> str | None:
    path = Path(matrix_cache_dir) / "metadata.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload.get("source_content_hash") or payload.get("data_freeze_hash") or payload.get("source_manifest_hash") or payload.get("cache_hash")


def diff_matrix_source(data_dir: str | Path, matrix_cache_dir: str | Path, data_version_manifest_path: str | Path | None = None) -> MatrixSourceDiff:
    source_hash = compute_source_hash(data_dir, data_version_manifest_path)
    matrix_hash = matrix_metadata_hash(matrix_cache_dir)
    issues: list[dict] = []
    if source_hash is None:
        issues.append({"severity": "error", "code": "missing_source_hash", "message": "source data hash is unavailable"})
    if matrix_hash is None:
        issues.append({"severity": "warning", "code": "missing_matrix_hash", "message": "matrix metadata hash is unavailable"})
    drift = bool(source_hash and matrix_hash and source_hash != matrix_hash)
    if drift:
        issues.append({"severity": "warning", "code": "source_hash_drift", "message": "source data hash differs from matrix metadata"})
    status = "drift" if drift else "fresh" if source_hash and matrix_hash else "unknown"
    return MatrixSourceDiff(status=status, source_hash=source_hash, matrix_hash=matrix_hash, drift_count=1 if drift else 0, issues=issues)
