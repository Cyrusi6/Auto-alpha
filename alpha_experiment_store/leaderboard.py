"""Leaderboard and validation candidate pool helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_jsonl_artifact
from evaluation import bounded_factor_score
from factor_store import FactorRecord, LocalFactorStore, has_positive_oos_evidence, validation_admission_reason

from .models import AlphaLeaderboardRecord


def build_leaderboard_from_factor_store(
    factor_store_dir: str | Path,
    *,
    top_k: int = 100,
    campaign_id: str = "",
) -> list[AlphaLeaderboardRecord]:
    store = LocalFactorStore(factor_store_dir)
    return build_leaderboard(store.load_factors(), top_k=top_k, factor_store_dir=str(factor_store_dir), campaign_id=campaign_id)


def build_leaderboard(
    factors: list[FactorRecord],
    *,
    top_k: int = 100,
    factor_store_dir: str = "",
    campaign_id: str = "",
) -> list[AlphaLeaderboardRecord]:
    rows: list[AlphaLeaderboardRecord] = []
    for factor in factors:
        components = _score_components(factor)
        final = components["standardized_final_score"]
        metadata = dict(factor.metadata or {})
        metadata.update(
            {
                "factor_store_dir": factor_store_dir,
                "campaign_id": campaign_id or metadata.get("alpha_campaign_id", ""),
                "feature_version": factor.feature_version,
                "formula_names": list(factor.formula),
                "factor_values_path": str(Path(factor_store_dir) / "factor_values" / f"{factor.factor_id}.jsonl") if factor_store_dir else "",
                "factor_status": factor.status,
            }
        )
        rows.append(
            AlphaLeaderboardRecord(
                rank=0,
                factor_id=factor.factor_id,
                formula_hash=factor.formula_hash,
                final_score=float(final),
                score_components=components,
                validation_ready=has_positive_oos_evidence(factor),
                reason=_leaderboard_reason(factor, components),
                metadata=metadata,
            )
        )
    ordered = sorted(rows, key=lambda row: (row.validation_ready, row.final_score, row.factor_id), reverse=True)
    limited = ordered[: max(0, int(top_k or len(ordered)))]
    return [
        AlphaLeaderboardRecord(
            rank=idx + 1,
            factor_id=row.factor_id,
            formula_hash=row.formula_hash,
            final_score=row.final_score,
            score_components=row.score_components,
            validation_ready=row.validation_ready,
            reason=row.reason,
            metadata=row.metadata,
        )
        for idx, row in enumerate(limited)
    ]


def write_leaderboard(records: list[AlphaLeaderboardRecord], output_dir: str | Path) -> Path:
    return write_jsonl_artifact(Path(output_dir) / "alpha_leaderboard.jsonl", records, "alpha_leaderboard", "alpha_experiment_store")


def write_validation_candidate_pool(
    leaderboard: list[AlphaLeaderboardRecord],
    output_dir: str | Path,
    *,
    max_candidates: int = 50,
    factor_store_dir: str = "",
) -> tuple[Path, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    seen_family: dict[str, int] = {}
    for row in leaderboard:
        if not row.validation_ready:
            continue
        metadata = dict(row.metadata or {})
        family = _family(metadata)
        if seen_family.get(family, 0) >= 10:
            continue
        seen_family[family] = seen_family.get(family, 0) + 1
        store_dir = metadata.get("factor_store_dir") or factor_store_dir
        records.append(
            {
                "factor_id": row.factor_id,
                "formula_hash": row.formula_hash,
                "formula_names": metadata.get("formula_names", []),
                "feature_version": metadata.get("feature_version", ""),
                "source_campaign": metadata.get("campaign_id", ""),
                "rank": row.rank,
                "final_score": row.final_score,
                "score_components": row.score_components,
                "factor_store_dir": str(store_dir),
                "factor_values_path": metadata.get("factor_values_path")
                or str(Path(str(store_dir)) / "factor_values" / f"{row.factor_id}.jsonl"),
                "recommended_validation_split": "walk_forward_long_history",
                "family": family,
                "metadata": metadata,
                "factor_status": metadata.get("factor_status", ""),
            }
        )
        if len(records) >= max(0, int(max_candidates)):
            break
    path = write_jsonl_artifact(Path(output_dir) / "alpha_validation_candidate_pool.jsonl", records, "alpha_validation_candidate_pool", "alpha_experiment_store")
    return path, records


def load_candidate_pool(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def _score_components(factor: FactorRecord) -> dict[str, float]:
    metrics = factor.metrics or {}
    metadata = factor.metadata or {}
    complexity = float(metadata.get("formula_complexity", metadata.get("complexity", len(factor.formula_tokens))) or 0.0)
    max_corr = abs(float(metadata.get("max_abs_correlation", 0.0) or 0.0))
    leakage_status = str(metadata.get("leakage_status", metadata.get("pit_status", "passed")) or "passed")
    campaign_score = metadata.get("final_score")
    score_method = str(((metadata.get("score_components") or {}).get("score_method") or ""))
    if campaign_score is not None and score_method == "dimensionless_cohort_multi_objective_v1":
        standardized = float(campaign_score)
    else:
        standardized, _ = bounded_factor_score(metrics)
    return {
        "standardized_final_score": float(standardized),
        "base_score": float(standardized),
        "coverage": float(metrics.get("coverage", metrics.get("coverage_ratio", 0.0)) or 0.0),
        "turnover": float(metrics.get("turnover", 0.0) or 0.0),
        "complexity": complexity,
        "correlation_penalty": max(0.0, max_corr - 0.80),
        "novelty": float(metadata.get("novelty_score", 0.0) or 0.0),
        "diversity": 1.0 if metadata.get("diversity_group") else 0.0,
        "pit_penalty": 0.0 if leakage_status in {"passed", "ok", "ready", ""} else 1.0,
    }


def _leaderboard_reason(factor: FactorRecord, components: dict[str, float]) -> str:
    admission_reason = validation_admission_reason(factor)
    if admission_reason != "validation_candidate_admitted":
        return admission_reason
    if components["correlation_penalty"] > 0:
        return "correlation penalty applied"
    if components["pit_penalty"] > 0:
        return "PIT readiness penalty applied"
    return "validation ready"


def _family(metadata: dict[str, Any]) -> str:
    tags = metadata.get("alpha_family_tags") or metadata.get("family_tags") or []
    if isinstance(tags, list) and tags:
        return str(tags[0])
    return str(metadata.get("family", "general") or "general")
