"""Ingest Alpha Factory outputs into the local experiment warehouse."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factor_store import LocalFactorStore

from .consolidate import consolidate_factor_stores, discover_shard_factor_stores
from .leaderboard import build_leaderboard_from_factor_store, write_leaderboard, write_validation_candidate_pool
from .models import AlphaExperimentRecord, AlphaShardRecord
from .registry import LocalAlphaExperimentStore
from .report import write_store_report


def ingest_alpha_factory_run(
    store_dir: str | Path,
    *,
    campaign_report_path: str | Path | None = None,
    campaign_manifest_path: str | Path | None = None,
    paths: dict[str, str] | None = None,
    shard_factor_store_dirs: list[str | Path] | None = None,
    experiment_id: str | None = None,
    consolidate_shards: bool = False,
    consolidated_factor_store_dir: str | Path | None = None,
    write_leaderboard_flag: bool = False,
    validation_candidate_pool_dir: str | Path | None = None,
    leaderboard_top_k: int = 100,
    max_validation_candidates: int = 50,
    previous_experiment_dirs: list[str | Path] | None = None,
) -> dict[str, Any]:
    store = LocalAlphaExperimentStore(store_dir)
    report = _read_json(campaign_report_path)
    manifest = _read_json(campaign_manifest_path) or _read_json((paths or {}).get("alpha_campaign_manifest_path"))
    campaign_id = str(report.get("campaign_id") or manifest.get("campaign_id") or experiment_id or "alpha_campaign")
    record = AlphaExperimentRecord(
        experiment_id=experiment_id or campaign_id,
        campaign_id=campaign_id,
        campaign_name=str(manifest.get("campaign_name") or "alpha_campaign"),
        data_freeze_id=manifest.get("data_freeze_id"),
        data_freeze_hash=manifest.get("data_freeze_hash"),
        feature_set_name=manifest.get("feature_set_name"),
        feature_set_hash=manifest.get("feature_version"),
        candidate_budget=int((manifest.get("generator_budgets") or {}).get("candidate_budget", 0) or 0),
        shard_count=int((manifest.get("compute_config") or {}).get("shard_count", 0) or 0),
        compute_run_id=(report.get("summary") or {}).get("compute_run_report_path"),
        status=str(report.get("status") or "registered"),
        created_at=str(manifest.get("created_at") or report.get("created_at") or ""),
        source_paths={k: str(v) for k, v in (paths or {}).items() if v},
        metadata={"summary": report.get("summary", {}), "warnings": report.get("warnings", [])},
    )
    store.register_experiment(record)

    discovered = list(shard_factor_store_dirs or [])
    discovered.extend(discover_shard_factor_stores(paths, Path(campaign_report_path).parent if campaign_report_path else None))
    if not discovered and paths and paths.get("factor_store_dir"):
        discovered.append(paths["factor_store_dir"])
    unique_dirs = _unique_paths(discovered)
    for idx, shard_dir in enumerate(unique_dirs):
        shard = _shard_record(shard_dir, record.experiment_id, idx, len(unique_dirs), paths or {})
        store.register_shard(shard)

    output_factor_store_dir = Path(consolidated_factor_store_dir) if consolidated_factor_store_dir else store.root_dir / "consolidated_factor_store"
    dedup_report: dict[str, Any] = {}
    if consolidate_shards and unique_dirs:
        dedup_report = consolidate_factor_stores(
            unique_dirs,
            output_factor_store_dir,
            experiment_id=record.experiment_id,
            campaign_id=campaign_id,
            report_dir=store.root_dir,
        )
        store.write_consolidated_factors(dedup_report.get("consolidated_factors", []))
    factor_store_for_leaderboard = output_factor_store_dir if (output_factor_store_dir / "factors.jsonl").exists() else None
    if write_leaderboard_flag and factor_store_for_leaderboard:
        leaderboard = build_leaderboard_from_factor_store(factor_store_for_leaderboard, top_k=leaderboard_top_k, campaign_id=campaign_id)
        store.write_leaderboard(leaderboard)
        write_leaderboard(leaderboard, store.root_dir)
        pool_dir = Path(validation_candidate_pool_dir) if validation_candidate_pool_dir else store.root_dir
        pool_path, pool_records = write_validation_candidate_pool(
            leaderboard,
            pool_dir,
            max_candidates=max_validation_candidates,
            factor_store_dir=str(factor_store_for_leaderboard),
        )
        if pool_dir != store.root_dir:
            store.write_validation_candidate_pool(pool_records)
        else:
            store.write_validation_candidate_pool(pool_records)
    report_json, report_md = write_store_report(store, {"dedup_report": dedup_report, "previous_experiment_dirs": [str(p) for p in previous_experiment_dirs or []]})
    store.write_registry_summary()
    return {
        "status": "success",
        "experiment_id": record.experiment_id,
        "campaign_id": campaign_id,
        "shard_count": len(unique_dirs),
        "consolidated_factor_store_dir": str(output_factor_store_dir),
        "dedup_report": dedup_report,
        "paths": {
            "alpha_experiment_registry_path": str(store.registry_path),
            "alpha_experiment_store_report_path": str(report_json),
            "alpha_experiment_store_report_md_path": str(report_md),
            "alpha_experiments_path": str(store.experiments_path),
            "alpha_shards_path": str(store.shards_path),
            "alpha_consolidated_factors_path": str(store.consolidated_path),
            "alpha_leaderboard_path": str(store.leaderboard_path),
            "alpha_validation_candidate_pool_path": str(store.validation_pool_path),
            "alpha_factor_dedup_report_path": str(store.root_dir / "alpha_factor_dedup_report.json"),
        },
    }


def _shard_record(shard_dir: str | Path, experiment_id: str, idx: int, count: int, paths: dict[str, str]) -> AlphaShardRecord:
    store_dir = Path(shard_dir)
    store = LocalFactorStore(store_dir)
    factors = store.load_factors()
    status_counts: dict[str, int] = {}
    for factor in factors:
        status_counts[factor.status] = status_counts.get(factor.status, 0) + 1
    output_dir = store_dir.parent / "output" if store_dir.name == "factor_store" else store_dir.parent
    result_path = output_dir / "formula_batch_eval_result.json"
    eval_results_path = output_dir / "formula_eval_results.jsonl"
    payload = _read_json(result_path)
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    return AlphaShardRecord(
        shard_id=f"{experiment_id}_shard_{idx:04d}",
        experiment_id=experiment_id,
        shard_index=idx,
        shard_count=count,
        formula_count=len(factors),
        evaluated_count=int(summary.get("total", len(factors)) or len(factors)),
        approved_count=status_counts.get("approved", 0),
        rejected_count=status_counts.get("rejected", 0),
        error_count=status_counts.get("error", 0),
        factor_store_dir=str(store_dir),
        batch_eval_result_path=str(result_path) if result_path.exists() else None,
        eval_results_path=str(eval_results_path) if eval_results_path.exists() else None,
        compute_job_id=_compute_job_id(paths, idx),
        status="success" if result_path.exists() or factors else "registered",
        metadata={"status_counts": status_counts},
    )


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def _unique_paths(paths: list[str | Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        target = Path(path)
        key = str(target.resolve()) if target.exists() else str(target)
        if key not in seen and target.exists():
            seen.add(key)
            unique.append(target)
    return unique


def _compute_job_id(paths: dict[str, str], idx: int) -> str | None:
    runs_path = paths.get("compute_job_runs_path")
    if not runs_path or not Path(runs_path).exists():
        return None
    rows = [json.loads(line) for line in Path(runs_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if idx < len(rows):
        return str(rows[idx].get("job_id") or "")
    return None
