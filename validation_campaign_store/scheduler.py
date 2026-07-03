"""Shard planning and validation_lab execution for validation campaigns."""

from __future__ import annotations

import json
import contextlib
import io
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_jsonl_artifact
from validation_lab.run_validation import main as validation_lab_main

from .models import ValidationShardRecord
from .registry import LocalValidationCampaignStore


def plan_validation_shards(
    store_dir: str | Path,
    output_dir: str | Path,
    *,
    validation_campaign_id: str,
    shard_count: int = 1,
    max_candidates_per_shard: int | None = None,
) -> list[ValidationShardRecord]:
    store = LocalValidationCampaignStore(store_dir)
    candidates = store.load_candidates()
    if max_candidates_per_shard and max_candidates_per_shard > 0:
        shard_count = max(shard_count, (len(candidates) + max_candidates_per_shard - 1) // max_candidates_per_shard)
    shard_count = max(1, int(shard_count or 1))
    shards: list[ValidationShardRecord] = []
    for idx in range(shard_count):
        rows = candidates[idx::shard_count]
        shard_dir = Path(output_dir) / "validation_shards" / f"shard_{idx:04d}"
        shard_dir.mkdir(parents=True, exist_ok=True)
        pool_path = shard_dir / "candidate_pool.jsonl"
        write_jsonl_artifact(pool_path, [_candidate_to_pool_row(row) for row in rows], "alpha_validation_candidate_pool", "validation_campaign_store")
        shards.append(
            ValidationShardRecord(
                shard_id=f"{validation_campaign_id}_shard_{idx:04d}",
                validation_campaign_id=validation_campaign_id,
                shard_index=idx,
                shard_count=shard_count,
                candidate_count=len(rows),
                output_dir=str(shard_dir),
                status="planned",
                metadata={"candidate_pool_path": str(pool_path)},
            )
        )
    store.write_shards(shards)
    return shards


def run_validation_shards(
    store_dir: str | Path,
    *,
    data_dir: str,
    factor_store_dir: str,
    output_dir: str | Path,
    validation_campaign_id: str,
    shard_count: int = 1,
    max_candidates_per_shard: int | None = None,
    split_method: str = "simple_walk_forward",
    run_multiple_testing: bool = False,
    run_overfit_risk: bool = False,
    run_placebo: bool = False,
    placebo_trials: int = 12,
    run_regime: bool = False,
    run_sensitivity: bool = False,
    run_stress_backtest: bool = False,
    resume: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    store = LocalValidationCampaignStore(store_dir)
    shards = plan_validation_shards(
        store_dir,
        output_dir,
        validation_campaign_id=validation_campaign_id,
        shard_count=shard_count,
        max_candidates_per_shard=max_candidates_per_shard,
    )
    if dry_run:
        return {"status": "planned", "shard_count": len(shards), "paths": store.paths()}

    updated: list[ValidationShardRecord] = []
    for shard in shards:
        shard_dir = Path(shard.output_dir)
        report_path = shard_dir / "validation_candidate_pool_report.json"
        if resume and report_path.exists():
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            updated.append(_shard_from_payload(shard, payload, "success"))
            continue
        argv = [
            "validate-candidates",
            "--data-dir",
            data_dir,
            "--factor-store-dir",
            factor_store_dir,
            "--validation-candidate-pool-path",
            str(shard_dir / "candidate_pool.jsonl"),
            "--output-dir",
            str(shard_dir),
            "--split-method",
            split_method,
            "--train-size",
            "1",
            "--test-size",
            "1",
        ]
        if run_multiple_testing:
            argv.append("--run-multiple-testing")
        if run_overfit_risk:
            argv.append("--run-overfit-risk")
        if run_placebo:
            argv.extend(["--run-placebo", "--placebo-trials", str(placebo_trials)])
        if run_regime:
            argv.append("--run-regime")
        if run_sensitivity:
            argv.append("--run-sensitivity")
        if run_stress_backtest:
            argv.append("--run-stress-backtest")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = validation_lab_main(argv)
        if code == 0 and report_path.exists():
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            updated.append(_shard_from_payload(shard, payload, "success"))
        else:
            updated.append(
                ValidationShardRecord(
                    **{
                        **shard.to_dict(),
                        "status": "failed",
                        "failed_count": shard.candidate_count,
                        "error": f"validation_lab exit code {code}",
                    }
                )
            )
    store.write_shards(updated)
    return {
        "status": "success" if all(row.status == "success" for row in updated) else "partial",
        "shard_count": len(updated),
        "success_count": sum(row.success_count for row in updated),
        "failed_count": sum(row.failed_count for row in updated),
        "paths": store.paths(),
    }


def _candidate_to_pool_row(row: dict[str, Any]) -> dict[str, Any]:
    source = row.get("metadata", {}).get("source_candidate", {}) if isinstance(row.get("metadata"), dict) else {}
    return {
        **source,
        "factor_id": row.get("factor_id"),
        "formula_hash": row.get("formula_hash"),
        "formula_names": row.get("formula_names", []),
        "feature_version": row.get("feature_version", ""),
        "source_campaign": row.get("source_campaign_id", ""),
        "rank": row.get("alpha_rank", 0),
        "final_score": row.get("alpha_score", 0.0),
        "factor_store_dir": row.get("factor_store_dir", ""),
        "factor_values_path": row.get("factor_values_path", ""),
        "family": (row.get("family_tags") or ["general"])[0],
    }


def _shard_from_payload(shard: ValidationShardRecord, payload: dict[str, Any], status: str) -> ValidationShardRecord:
    return ValidationShardRecord(
        shard_id=shard.shard_id,
        validation_campaign_id=shard.validation_campaign_id,
        shard_index=shard.shard_index,
        shard_count=shard.shard_count,
        candidate_count=shard.candidate_count,
        success_count=int(payload.get("validated_candidate_count", 0) or 0) - int(payload.get("blocked_count", 0) or 0),
        failed_count=int(payload.get("blocked_count", 0) or 0),
        skipped_count=0,
        output_dir=shard.output_dir,
        validation_lab_report_path=str(Path(shard.output_dir) / "validation_candidate_pool_report.json"),
        status=status,
        error=None,
        metadata=shard.metadata | {"payload": payload},
    )
