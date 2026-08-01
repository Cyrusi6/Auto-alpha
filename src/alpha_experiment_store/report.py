"""Reports for the local Alpha experiment warehouse."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .registry import LocalAlphaExperimentStore


def write_store_report(store: LocalAlphaExperimentStore, extra: dict[str, Any] | None = None) -> tuple[Path, Path]:
    experiments = [item.to_dict() for item in store.load_experiments()]
    shards = [item.to_dict() for item in store.load_shards()]
    consolidated = store.load_consolidated_factors()
    leaderboard = store.load_leaderboard()
    candidate_pool = store.load_validation_candidate_pool()
    failed_shards = [row for row in shards if str(row.get("status")) in {"failed", "error"}]
    payload = {
        "status": "partial" if failed_shards else ("ready" if leaderboard else "registered"),
        "experiment_count": len(experiments),
        "shard_count": len(shards),
        "failed_shard_count": len(failed_shards),
        "consolidated_factor_count": len(consolidated),
        "leaderboard_count": len(leaderboard),
        "validation_candidate_count": len(candidate_pool),
        "experiments": experiments,
        "summary": {
            "leaderboard_empty": len(leaderboard) == 0,
            "validation_pool_ready": len(candidate_pool) > 0,
        },
        "paths": {
            "alpha_experiment_registry_path": str(store.registry_path),
            "alpha_experiments_path": str(store.experiments_path),
            "alpha_shards_path": str(store.shards_path),
            "alpha_consolidated_factors_path": str(store.consolidated_path),
            "alpha_leaderboard_path": str(store.leaderboard_path),
            "alpha_validation_candidate_pool_path": str(store.validation_pool_path),
        },
        "extra": extra or {},
    }
    json_path = write_json_artifact(store.report_path, payload, "alpha_experiment_store_report", "alpha_experiment_store")
    md_path = store.root_dir / "alpha_experiment_store_report.md"
    md_path.write_text(_markdown(payload), encoding="utf-8")
    return json_path, md_path


def _markdown(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Alpha Experiment Store Report",
            "",
            f"- Status: {payload.get('status')}",
            f"- Experiments: {payload.get('experiment_count', 0)}",
            f"- Shards: {payload.get('shard_count', 0)}",
            f"- Failed shards: {payload.get('failed_shard_count', 0)}",
            f"- Consolidated factors: {payload.get('consolidated_factor_count', 0)}",
            f"- Leaderboard rows: {payload.get('leaderboard_count', 0)}",
            f"- Validation candidates: {payload.get('validation_candidate_count', 0)}",
            "",
        ]
    )
