"""Local registry for Alpha Factory campaign warehouse records."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import (
    AlphaConsolidatedFactorRecord,
    AlphaExperimentRecord,
    AlphaLeaderboardRecord,
    AlphaShardRecord,
    AlphaStoreWriteResult,
)


class LocalAlphaExperimentStore:
    """Small JSON/JSONL warehouse for campaign-level Alpha Factory outputs."""

    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.experiments_path = self.root_dir / "alpha_experiments.jsonl"
        self.shards_path = self.root_dir / "alpha_shards.jsonl"
        self.consolidated_path = self.root_dir / "alpha_consolidated_factors.jsonl"
        self.leaderboard_path = self.root_dir / "alpha_leaderboard.jsonl"
        self.registry_path = self.root_dir / "alpha_experiment_registry.json"
        self.validation_pool_path = self.root_dir / "alpha_validation_candidate_pool.jsonl"
        self.report_path = self.root_dir / "alpha_experiment_store_report.json"

    def register_experiment(self, record: AlphaExperimentRecord) -> AlphaStoreWriteResult:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        records = self.load_experiments()
        payloads = [item.to_dict() for item in records if item.experiment_id != record.experiment_id]
        payloads.append(record.to_dict())
        write_jsonl_artifact(self.experiments_path, payloads, "alpha_experiments", "alpha_experiment_store")
        self.write_registry_summary()
        return AlphaStoreWriteResult(str(self.experiments_path), 1)

    def register_shard(self, record: AlphaShardRecord) -> AlphaStoreWriteResult:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        records = self.load_shards()
        payloads = [item.to_dict() for item in records if item.shard_id != record.shard_id]
        payloads.append(record.to_dict())
        write_jsonl_artifact(self.shards_path, payloads, "alpha_shards", "alpha_experiment_store")
        self.write_registry_summary()
        return AlphaStoreWriteResult(str(self.shards_path), 1)

    def write_consolidated_factors(self, records: Iterable[AlphaConsolidatedFactorRecord | dict[str, Any]]) -> AlphaStoreWriteResult:
        payloads = [_to_payload(item) for item in records]
        write_jsonl_artifact(self.consolidated_path, payloads, "alpha_consolidated_factors", "alpha_experiment_store")
        self.write_registry_summary()
        return AlphaStoreWriteResult(str(self.consolidated_path), len(payloads))

    def write_leaderboard(self, records: Iterable[AlphaLeaderboardRecord | dict[str, Any]]) -> AlphaStoreWriteResult:
        payloads = [_to_payload(item) for item in records]
        write_jsonl_artifact(self.leaderboard_path, payloads, "alpha_leaderboard", "alpha_experiment_store")
        self.write_registry_summary()
        return AlphaStoreWriteResult(str(self.leaderboard_path), len(payloads))

    def write_validation_candidate_pool(self, records: Iterable[dict[str, Any]]) -> AlphaStoreWriteResult:
        payloads = [dict(item) for item in records]
        write_jsonl_artifact(self.validation_pool_path, payloads, "alpha_validation_candidate_pool", "alpha_experiment_store")
        self.write_registry_summary()
        return AlphaStoreWriteResult(str(self.validation_pool_path), len(payloads))

    def load_experiments(self) -> list[AlphaExperimentRecord]:
        return [AlphaExperimentRecord(**_experiment_defaults(row)) for row in _read_jsonl(self.experiments_path)]

    def load_shards(self) -> list[AlphaShardRecord]:
        return [AlphaShardRecord(**_shard_defaults(row)) for row in _read_jsonl(self.shards_path)]

    def load_consolidated_factors(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.consolidated_path)

    def load_leaderboard(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.leaderboard_path)

    def load_validation_candidate_pool(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.validation_pool_path)

    def write_registry_summary(self) -> Path:
        experiments = [item.to_dict() for item in self.load_experiments()]
        shards = [item.to_dict() for item in self.load_shards()]
        consolidated = self.load_consolidated_factors()
        leaderboard = self.load_leaderboard()
        payload = {
            "status": _registry_status(experiments, shards, leaderboard),
            "experiment_count": len(experiments),
            "shard_count": len(shards),
            "consolidated_factor_count": len(consolidated),
            "leaderboard_count": len(leaderboard),
            "validation_candidate_count": len(self.load_validation_candidate_pool()),
            "experiments": experiments,
            "paths": {
                "alpha_experiments_path": str(self.experiments_path),
                "alpha_shards_path": str(self.shards_path),
                "alpha_consolidated_factors_path": str(self.consolidated_path),
                "alpha_leaderboard_path": str(self.leaderboard_path),
                "alpha_validation_candidate_pool_path": str(self.validation_pool_path),
            },
        }
        return write_json_artifact(self.registry_path, payload, "alpha_experiment_registry", "alpha_experiment_store")


def _to_payload(record: object) -> dict[str, Any]:
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)
    if hasattr(record, "to_dict"):
        return dict(record.to_dict())
    if isinstance(record, dict):
        return dict(record)
    raise TypeError(f"unsupported record: {type(record)!r}")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _experiment_defaults(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload.setdefault("data_freeze_id", None)
    payload.setdefault("data_freeze_hash", None)
    payload.setdefault("feature_set_name", None)
    payload.setdefault("feature_set_hash", None)
    payload.setdefault("matrix_cache_id", None)
    payload.setdefault("matrix_cache_hash", None)
    payload.setdefault("candidate_budget", 0)
    payload.setdefault("shard_count", 0)
    payload.setdefault("compute_run_id", None)
    payload.setdefault("status", "registered")
    payload.setdefault("created_at", "")
    payload.setdefault("source_paths", {})
    payload.setdefault("metadata", {})
    return payload


def _shard_defaults(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload.setdefault("formula_count", 0)
    payload.setdefault("evaluated_count", 0)
    payload.setdefault("approved_count", 0)
    payload.setdefault("rejected_count", 0)
    payload.setdefault("error_count", 0)
    payload.setdefault("factor_store_dir", None)
    payload.setdefault("batch_eval_result_path", None)
    payload.setdefault("eval_results_path", None)
    payload.setdefault("compute_job_id", None)
    payload.setdefault("status", "registered")
    payload.setdefault("error", None)
    payload.setdefault("metadata", {})
    return payload


def _registry_status(experiments: list[dict[str, Any]], shards: list[dict[str, Any]], leaderboard: list[dict[str, Any]]) -> str:
    if any(str(item.get("status")) in {"failed", "error"} for item in shards):
        return "partial"
    if experiments and leaderboard:
        return "ready"
    if experiments:
        return "registered"
    return "empty"
