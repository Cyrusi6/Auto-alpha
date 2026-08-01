"""Immutable content-addressed overlays for normalized factor records."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable


def publish_normalized_factor_overlay(
    output_root: str | Path,
    records: Iterable[dict[str, Any]],
    *,
    source_lineage: dict[str, str],
    semantics_contract_hash: str,
) -> dict[str, Any]:
    """Publish an immutable normalized overlay without touching the source store."""

    root = Path(output_root)
    canonical_records = sorted(
        (dict(record) for record in records),
        key=lambda row: (str(row.get("formula_hash", "")), str(row.get("factor_id", ""))),
    )
    records_bytes = b"".join(_canonical_json(row) + b"\n" for row in canonical_records)
    records_sha256 = hashlib.sha256(records_bytes).hexdigest()
    content_payload = {
        "overlay_version": "task054b_normalized_factor_overlay_v1",
        "records_sha256": records_sha256,
        "record_count": len(canonical_records),
        "semantics_contract_hash": semantics_contract_hash,
        "source_lineage": dict(sorted(source_lineage.items())),
    }
    content_hash = hashlib.sha256(_canonical_json(content_payload)).hexdigest()
    generation_id = f"normalized_factors_{content_hash[:24]}"
    generations = root / "generations"
    target = generations / generation_id
    manifest = content_payload | {
        "generation_id": generation_id,
        "content_hash": content_hash,
        "records_file": "normalized_factors.jsonl",
    }

    if target.exists():
        _validate_existing(target, manifest, records_sha256)
    else:
        generations.mkdir(parents=True, exist_ok=True)
        staging = generations / f".{generation_id}.{uuid.uuid4().hex}.staging"
        staging.mkdir(parents=False, exist_ok=False)
        try:
            (staging / "normalized_factors.jsonl").write_bytes(records_bytes)
            (staging / "overlay_manifest.json").write_bytes(_pretty_json(manifest))
            os.replace(staging, target)
        finally:
            if staging.exists():
                for path in staging.iterdir():
                    path.unlink()
                staging.rmdir()

    pointer = {
        "generation_id": generation_id,
        "content_hash": content_hash,
        "manifest": f"generations/{generation_id}/overlay_manifest.json",
    }
    root.mkdir(parents=True, exist_ok=True)
    pointer_tmp = root / f".current.{uuid.uuid4().hex}.tmp"
    pointer_tmp.write_bytes(_pretty_json(pointer))
    os.replace(pointer_tmp, root / "current.json")
    return manifest | {"generation_dir": str(target)}


def _validate_existing(target: Path, expected: dict[str, Any], records_sha256: str) -> None:
    manifest_path = target / "overlay_manifest.json"
    records_path = target / "normalized_factors.jsonl"
    if not manifest_path.is_file() or not records_path.is_file():
        raise RuntimeError("normalized_overlay_generation_incomplete")
    actual = json.loads(manifest_path.read_text(encoding="utf-8"))
    if actual != expected:
        raise RuntimeError("normalized_overlay_generation_collision")
    if hashlib.sha256(records_path.read_bytes()).hexdigest() != records_sha256:
        raise RuntimeError("normalized_overlay_records_sha_mismatch")


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
