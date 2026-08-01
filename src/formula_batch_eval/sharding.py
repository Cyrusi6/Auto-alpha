"""Deterministic sharding for formula batch evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact

from .models import FormulaEvalRequest


def select_shard_requests(requests: list[FormulaEvalRequest], shard_id: int | None, shard_count: int | None) -> list[FormulaEvalRequest]:
    if shard_id is None or not shard_count or shard_count <= 1:
        return requests
    selected = []
    for request in requests:
        bucket = int(hashlib.sha256(request.formula_hash.encode("utf-8")).hexdigest()[:8], 16) % int(shard_count)
        if bucket == int(shard_id):
            selected.append(request)
    return selected


def write_shard_manifest(
    requests: list[FormulaEvalRequest],
    output_dir: str | Path,
    shard_id: int,
    shard_count: int,
    source_path: str | None = None,
    manifest_path: str | Path | None = None,
) -> dict:
    payload = {
        "shard_id": int(shard_id),
        "shard_count": int(shard_count),
        "record_count": len(requests),
        "source_path": source_path,
        "source_hash": _sha256(Path(source_path)) if source_path and Path(source_path).exists() else "",
        "formula_hashes": [request.formula_hash for request in requests],
    }
    payload["shard_hash"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    path = Path(manifest_path) if manifest_path is not None else Path(output_dir) / "shard_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_artifact(path, payload, "formula_eval_shard_manifest", "formula_batch_eval")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
