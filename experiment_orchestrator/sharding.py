"""Deterministic sharding helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import ExperimentShard


def shard_formula_corpus(corpus_path: str | Path, shard_count: int, output_dir: str | Path) -> list[ExperimentShard]:
    rows = _read_jsonl(Path(corpus_path))
    return _write_shards(rows, Path(corpus_path), max(1, shard_count), Path(output_dir), "formula_corpus")


def shard_candidates_json(candidates_json: str | Path, shard_count: int, output_dir: str | Path) -> list[ExperimentShard]:
    payload = json.loads(Path(candidates_json).read_text(encoding="utf-8"))
    rows = payload.get("candidates", payload) if isinstance(payload, dict) else payload
    return _write_shards(list(rows), Path(candidates_json), max(1, shard_count), Path(output_dir), "candidates")


def shard_formula_search_seed(seed: int, shard_count: int) -> list[int]:
    return [int(seed) + idx * 1009 for idx in range(max(1, shard_count))]


def shard_walk_forward_windows(walk_forward_config: dict, shard_count: int) -> list[dict]:
    windows = list(walk_forward_config.get("windows", []))
    shards = [[] for _ in range(max(1, shard_count))]
    for idx, window in enumerate(windows):
        shards[idx % len(shards)].append(window)
    return [{"shard_id": idx, "windows": rows} for idx, rows in enumerate(shards)]


def _write_shards(rows: list[dict], source: Path, shard_count: int, output_dir: Path, stage: str) -> list[ExperimentShard]:
    output_dir.mkdir(parents=True, exist_ok=True)
    buckets: list[list[dict]] = [[] for _ in range(shard_count)]
    for row in rows:
        key = str(row.get("formula_hash") or row.get("name") or row)
        index = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % shard_count
        buckets[index].append(row)
    source_hash = _sha256(source) if source.exists() else ""
    shards: list[ExperimentShard] = []
    for idx, bucket in enumerate(buckets):
        shard_dir = output_dir / f"shard_{idx}"
        shard_dir.mkdir(parents=True, exist_ok=True)
        records_path = shard_dir / "records.jsonl"
        write_jsonl_artifact(records_path, bucket, "formula_corpus", "experiment_orchestrator")
        shard_hash = hashlib.sha256(
            json.dumps({"source_hash": source_hash, "shard_id": idx, "records": bucket}, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        manifest = {
            "shard_id": idx,
            "shard_count": shard_count,
            "stage": stage,
            "record_count": len(bucket),
            "source_path": str(source),
            "source_hash": source_hash,
            "shard_hash": shard_hash,
            "records_path": str(records_path),
        }
        write_json_artifact(shard_dir / "shard_manifest.json", manifest, "experiment_shard_manifest", "experiment_orchestrator")
        shards.append(
            ExperimentShard(
                shard_id=idx,
                shard_count=shard_count,
                stage=stage,
                input_path=str(records_path),
                output_dir=str(shard_dir),
                shard_hash=shard_hash,
                record_count=len(bucket),
            )
        )
    return shards


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
