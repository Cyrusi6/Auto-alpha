"""Consolidate validation_lab shard outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .models import ValidationCandidateResult
from .registry import LocalValidationCampaignStore


def consolidate_validation_results(store_dir: str | Path, *, output_dir: str | Path | None = None) -> dict[str, Any]:
    store = LocalValidationCampaignStore(store_dir)
    candidates_by_factor = {row.get("factor_id"): row for row in store.load_candidates()}
    results: list[ValidationCandidateResult] = []
    for shard in store.load_shards():
        shard_dir = Path(str(shard.get("output_dir") or ""))
        result_path = shard_dir / "validation_candidate_pool_results.jsonl"
        for row in _read_jsonl(result_path):
            source = row.get("source_candidate", {}) if isinstance(row.get("source_candidate"), dict) else {}
            factor_id = str(row.get("factor_id") or source.get("factor_id") or "")
            candidate = candidates_by_factor.get(factor_id, {})
            paths = row.get("paths", {}) if isinstance(row.get("paths"), dict) else {}
            summary = row.get("validation_summary", {}) if isinstance(row.get("validation_summary"), dict) else {}
            if not summary and paths.get("factor_validation_summary_path"):
                summary = _read_json(paths.get("factor_validation_summary_path"))
            overfit = _read_json(paths.get("overfit_risk_report_path")) if paths.get("overfit_risk_report_path") else {}
            placebo = _read_json(paths.get("placebo_test_report_path")) if paths.get("placebo_test_report_path") else {}
            regime = _read_json(paths.get("regime_validation_report_path")) if paths.get("regime_validation_report_path") else {}
            sensitivity = _read_json(paths.get("sensitivity_report_path")) if paths.get("sensitivity_report_path") else {}
            stress = _read_json(paths.get("stress_backtest_report_path")) if paths.get("stress_backtest_report_path") else {}
            result = _candidate_result(row, candidate, summary, overfit, placebo, regime, sensitivity, stress)
            results.append(result)
    store.write_results(results)
    report = _aggregate_report(results)
    report_path = write_json_artifact(
        Path(output_dir or store.root_dir) / "validation_campaign_consolidation_report.json",
        report,
        "validation_campaign_consolidation_report",
        "validation_campaign_store",
    )
    return report | {"paths": store.paths() | {"validation_campaign_consolidation_report_path": str(report_path)}}


def _candidate_result(
    row: dict[str, Any],
    candidate: dict[str, Any],
    summary: dict[str, Any],
    overfit: dict[str, Any],
    placebo: dict[str, Any],
    regime: dict[str, Any],
    sensitivity: dict[str, Any],
    stress: dict[str, Any],
) -> ValidationCandidateResult:
    validation_status = str(row.get("status") or summary.get("status") or "partial")
    metrics = summary.get("metrics", {}) if isinstance(summary.get("metrics"), dict) else {}
    blocker_count = int(summary.get("blocker_count", row.get("validation_blocker_count", 0)) or 0)
    warning_count = int(summary.get("warning_count", 0) or 0)
    components = {
        "out_of_sample_score": float(summary.get("out_of_sample_score", 0.0) or 0.0),
        "mean_rank_ic": float(summary.get("mean_rank_ic", metrics.get("rank_ic", 0.0)) or 0.0),
        "icir": float(summary.get("mean_icir", metrics.get("rank_ic_ir", 0.0)) or 0.0),
        "window_pass_ratio": float(summary.get("window_pass_ratio", 0.0) or 0.0),
        "pbo_penalty": float(overfit.get("pbo_estimate", 0.0) or 0.0),
        "deflated_ic_score": float(overfit.get("deflated_ic_like_score", 0.0) or 0.0),
        "placebo_percentile": float(placebo.get("candidate_vs_placebo_percentile", 0.0) or 0.0),
        "regime_pass_ratio": float(regime.get("regime_pass_ratio", 0.0) or 0.0),
        "sensitivity_pass_ratio": float(sensitivity.get("sensitivity_pass_ratio", 0.0) or 0.0),
        "stress_pass_ratio": float(stress.get("stress_backtest_pass_ratio", 0.0) or 0.0),
        "blocker_penalty": float(blocker_count),
        "turnover_penalty": float(metrics.get("turnover", 0.0) or 0.0),
        "coverage": float(metrics.get("coverage", 0.0) or 0.0),
    }
    score = (
        components["out_of_sample_score"]
        + 0.20 * components["mean_rank_ic"]
        + 0.10 * components["icir"]
        + 0.10 * components["window_pass_ratio"]
        + 0.10 * components["deflated_ic_score"]
        + 0.10 * components["placebo_percentile"]
        + 0.05 * components["regime_pass_ratio"]
        + 0.05 * components["sensitivity_pass_ratio"]
        + 0.05 * components["stress_pass_ratio"]
        + 0.05 * components["coverage"]
        - 0.10 * components["pbo_penalty"]
        - 0.05 * components["turnover_penalty"]
        - 1.0 * components["blocker_penalty"]
    )
    return ValidationCandidateResult(
        validation_candidate_id=str(candidate.get("validation_candidate_id") or row.get("validation_candidate_id") or row.get("factor_id")),
        factor_id=str(row.get("factor_id") or candidate.get("factor_id") or ""),
        formula_hash=str(candidate.get("formula_hash") or (row.get("source_candidate") or {}).get("formula_hash") or ""),
        validation_status=validation_status,
        out_of_sample_score=components["out_of_sample_score"],
        rank_ic_mean=components["mean_rank_ic"],
        rank_ic_hit_rate=float(metrics.get("rank_ic_positive_ratio", 0.0) or 0.0),
        icir=components["icir"],
        pbo_estimate=components["pbo_penalty"],
        deflated_ic_score=components["deflated_ic_score"],
        placebo_percentile=components["placebo_percentile"],
        null_exceedance_ratio=float(placebo.get("null_exceedance_ratio", 0.0) or 0.0),
        regime_pass_ratio=components["regime_pass_ratio"],
        sensitivity_pass_ratio=components["sensitivity_pass_ratio"],
        stress_pass_ratio=components["stress_pass_ratio"],
        turnover_mean=components["turnover_penalty"],
        coverage_mean=components["coverage"],
        max_drawdown=float(metrics.get("max_drawdown", summary.get("max_single_window_loss", 0.0)) or 0.0),
        validation_score=float(score),
        blocker_count=blocker_count,
        warning_count=warning_count,
        selected_for_certification=False,
        metadata={"score_components": components, "source_result": row, "candidate": candidate},
    )


def _aggregate_report(results: list[ValidationCandidateResult]) -> dict[str, Any]:
    return {
        "status": "success",
        "candidate_count": len(results),
        "success_count": sum(1 for row in results if row.validation_status == "passed"),
        "failed_count": sum(1 for row in results if row.validation_status not in {"passed", "warning"}),
        "insufficient_data_count": sum(1 for row in results if row.warning_count > 0 and row.blocker_count == 0),
        "validation_blocker_count": sum(row.blocker_count for row in results),
        "pbo_distribution": _distribution([row.pbo_estimate for row in results]),
        "deflated_ic_distribution": _distribution([row.deflated_ic_score for row in results]),
        "placebo_distribution": _distribution([row.placebo_percentile for row in results]),
        "regime_pass_distribution": _distribution([row.regime_pass_ratio for row in results]),
        "stress_pass_distribution": _distribution([row.stress_pass_ratio for row in results]),
    }


def _distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0.0, "min": 0.0, "median": 0.0, "max": 0.0}
    ordered = sorted(float(v) for v in values)
    return {"count": float(len(ordered)), "min": ordered[0], "median": ordered[len(ordered) // 2], "max": ordered[-1]}


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
