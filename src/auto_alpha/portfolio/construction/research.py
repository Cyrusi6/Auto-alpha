"""Certified-factor portfolio research, admission, combination, walk-forward, and reporting."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


FACTOR_CERTIFIED_STATUS = "factor_certified"
SHADOW_CANDIDATE_STATUS = "shadow_candidate"
PORTFOLIO_REJECTED_STATUS = "portfolio_rejected"
DATA_BLOCKED_STATUS = "data_blocked"


class PortfolioResearchError(RuntimeError):
    """Raised when governed portfolio research must fail closed."""


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class StressScenario:
    scenario_id: str
    modeled_cost_multiplier: float = 1.0
    lagged_adv_multiplier: float = 1.0
    required_regime: str | None = None

    def __post_init__(self) -> None:
        if self.modeled_cost_multiplier <= 0.0 or self.lagged_adv_multiplier <= 0.0:
            raise ValueError("stress multipliers must be positive")


@dataclass(frozen=True)
class PortfolioResearchPolicy:
    policy_id: str = "factor_certified_portfolio_walk_forward_v1"
    train_size: int = 756
    validation_size: int = 126
    test_size: int = 126
    step_size: int = 126
    label_horizon: int = 2
    min_embargo: int = 2
    min_factor_count: int = 3
    min_family_count: int = 2
    min_cross_section_breadth: int = 30
    min_pair_observations: int = 252
    min_evaluable_windows: int = 3
    min_valid_test_dates: int = 114
    correlation_threshold: float = 0.70
    family_weight_cap: float = 0.50
    cluster_weight_cap: float = 0.60
    factor_weight_cap: float = 0.35
    weight_shrinkage: float = 0.25
    max_weight_change: float = 0.15
    min_positive_window_ratio: float = 0.60
    min_universe_pass_ratio: float = 1.0
    min_benchmark_pass_ratio: float = 1.0
    min_stress_pass_ratio: float = 1.0
    min_cost_adjusted_return: float = 0.0
    min_active_return: float = 0.0
    max_drawdown: float = 0.50
    top_n: int = 20
    max_stock_weight: float = 0.10
    initial_aum: float = 1_000_000.0
    lot_size: int = 100
    parameters_locked: bool = True
    certification_supported: bool = False
    shadow_only: bool = True
    paper_requires_independent_audit: bool = True
    required_scenarios: tuple[StressScenario, ...] = field(
        default_factory=lambda: (
            StressScenario("baseline"),
            StressScenario("double_modeled_cost", modeled_cost_multiplier=2.0),
            StressScenario("volume_down_50pct", lagged_adv_multiplier=0.50),
            StressScenario(
                "extreme_volatility",
                modeled_cost_multiplier=2.0,
                lagged_adv_multiplier=0.50,
                required_regime="extreme_volatility",
            ),
        )
    )

    def __post_init__(self) -> None:
        sizes = (self.train_size, self.test_size, self.step_size, self.label_horizon, self.min_embargo)
        if any(value < 1 for value in sizes) or self.validation_size < 0:
            raise ValueError("walk-forward sizes and horizon must be positive")
        if self.min_embargo < self.label_horizon:
            raise ValueError("portfolio embargo must cover label horizon")
        if self.min_factor_count < 2 or self.min_family_count < 1:
            raise ValueError("portfolio combination requires multiple certified factors")
        if self.min_cross_section_breadth < 2 or self.min_valid_test_dates < 1:
            raise ValueError("portfolio sample thresholds are invalid")
        bounded = (
            self.correlation_threshold,
            self.family_weight_cap,
            self.cluster_weight_cap,
            self.factor_weight_cap,
            self.weight_shrinkage,
            self.max_weight_change,
            self.min_positive_window_ratio,
            self.min_universe_pass_ratio,
            self.min_benchmark_pass_ratio,
            self.min_stress_pass_ratio,
            self.max_stock_weight,
        )
        if any(value < 0.0 or value > 1.0 for value in bounded):
            raise ValueError("portfolio policy ratios must be within [0, 1]")
        if not self.parameters_locked or self.certification_supported or not self.shadow_only:
            raise ValueError("production portfolio research must remain locked and shadow-only")
        scenario_ids = [scenario.scenario_id for scenario in self.required_scenarios]
        if scenario_ids != ["baseline", "double_modeled_cost", "volume_down_50pct", "extreme_volatility"]:
            raise ValueError("production stress scenario set is immutable")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def policy_hash(self) -> str:
        return stable_hash(self.to_dict())

    def effective_embargo(self, max_factor_lookback: int) -> int:
        return max(int(max_factor_lookback), self.label_horizon, self.min_embargo)


def validate_production_policy(policy: PortfolioResearchPolicy) -> None:
    if policy.policy_id != "factor_certified_portfolio_walk_forward_v1":
        raise PortfolioResearchError("production_portfolio_policy_id_invalid")
    if not policy.parameters_locked or not policy.shadow_only or policy.certification_supported:
        raise PortfolioResearchError("production_portfolio_policy_boundary_invalid")
    if policy.min_embargo < policy.label_horizon:
        raise PortfolioResearchError("portfolio_embargo_shorter_than_label_horizon")

from collections.abc import Mapping, Sequence
from typing import Any



def validate_factor_certified_records(
    records: Sequence[Mapping[str, Any]],
    *,
    min_factor_count: int,
    min_family_count: int,
) -> list[dict[str, Any]]:
    if len(records) < min_factor_count:
        raise PortfolioResearchError("certified_factor_count_below_policy")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for raw in records:
        row = dict(raw)
        factor_id = str(row.get("factor_id") or "")
        formula_hash = str(row.get("formula_hash") or "")
        status = str(row.get("status") or row.get("certification_status") or "")
        family = str(row.get("family") or "")
        if not factor_id or not formula_hash or len(formula_hash) != 64:
            raise PortfolioResearchError("factor_certified_identity_invalid")
        if status != FACTOR_CERTIFIED_STATUS:
            raise PortfolioResearchError(f"factor_not_factor_certified:{factor_id}:{status or 'missing'}")
        if factor_id in seen_ids or formula_hash in seen_hashes:
            raise PortfolioResearchError("factor_certified_pool_duplicate_identity")
        if not family:
            raise PortfolioResearchError(f"factor_certified_family_missing:{factor_id}")
        if str(row.get("sealed_holdout_status") or "") != "sealed_holdout_passed":
            raise PortfolioResearchError(f"factor_certified_holdout_evidence_missing:{factor_id}")
        if row.get("independent_audit_passed") is not True:
            raise PortfolioResearchError(f"factor_certified_independent_audit_missing:{factor_id}")
        evidence_hash = str(row.get("certification_evidence_hash") or "")
        if len(evidence_hash) != 64:
            raise PortfolioResearchError(f"factor_certified_evidence_hash_invalid:{factor_id}")
        lookback = int(row.get("effective_lookback") or row.get("lookback_days") or 0)
        if lookback < 0:
            raise PortfolioResearchError(f"factor_certified_lookback_invalid:{factor_id}")
        row["factor_id"] = factor_id
        row["formula_hash"] = formula_hash
        row["status"] = FACTOR_CERTIFIED_STATUS
        row["family"] = family
        row["effective_lookback"] = lookback
        normalized.append(row)
        seen_ids.add(factor_id)
        seen_hashes.add(formula_hash)
    families = {row["family"] for row in normalized}
    if len(families) < min_family_count:
        raise PortfolioResearchError("certified_factor_family_count_below_policy")
    return sorted(normalized, key=lambda row: row["factor_id"])

from dataclasses import asdict, dataclass
from typing import Any, Sequence

import numpy as np



@dataclass(frozen=True)
class CombinationFit:
    factor_ids: tuple[str, ...]
    families: tuple[str, ...]
    cluster_ids: tuple[int, ...]
    weights: tuple[float, ...]
    mean_rank_ic: tuple[float, ...]
    icir: tuple[float, ...]
    residual_coefficients: tuple[tuple[float, ...], ...]
    training_observations: tuple[int, ...]
    fit_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fit_factor_combination(
    values: np.ndarray,
    validity: np.ndarray,
    target: np.ndarray,
    target_validity: np.ndarray,
    common_cells: np.ndarray,
    train_indices: Sequence[int],
    factor_records: Sequence[dict[str, Any]],
    policy: PortfolioResearchPolicy,
    *,
    previous_weights: Sequence[float] | None = None,
) -> CombinationFit:
    factor_values, factor_validity, target_values, target_mask, common = _validate_inputs(
        values, validity, target, target_validity, common_cells, factor_records
    )
    train = np.asarray(tuple(train_indices), dtype=int)
    if train.size != policy.train_size or np.any(train < 0) or np.any(train >= factor_values.shape[1]):
        raise PortfolioResearchError("combination_training_axis_invalid")
    standardized, standardized_validity = standardize_factor_cube(
        factor_values,
        factor_validity & common[None, :, :],
        min_breadth=policy.min_cross_section_breadth,
    )
    correlations = factor_correlation_matrix(
        standardized,
        standardized_validity,
        train,
        min_observations=policy.min_pair_observations,
    )
    clusters = correlation_clusters(correlations, policy.correlation_threshold)
    residual_values, residual_validity, coefficients = residualize_by_cluster(
        standardized,
        standardized_validity,
        train,
        clusters,
    )
    means: list[float] = []
    icirs: list[float] = []
    observations: list[int] = []
    raw_scores: list[float] = []
    for factor_index in range(factor_values.shape[0]):
        series = _daily_rank_ic(
            residual_values[factor_index],
            target_values,
            residual_validity[factor_index] & target_mask & common,
            train,
            policy.min_cross_section_breadth,
        )
        if not series:
            raise PortfolioResearchError(f"factor_training_ic_unavailable:{factor_records[factor_index]['factor_id']}")
        mean_ic = float(np.mean(series))
        std_ic = float(np.std(series, ddof=1)) if len(series) > 1 else 0.0
        icir = mean_ic / std_ic * np.sqrt(len(series)) if std_ic > 1e-12 else (float("inf") if mean_ic > 0 else 0.0)
        score = max(mean_ic, 0.0) * max(min(icir, 20.0), 0.0)
        means.append(mean_ic)
        icirs.append(icir)
        observations.append(len(series))
        raw_scores.append(score)
    if not any(score > 0.0 and np.isfinite(score) for score in raw_scores):
        raise PortfolioResearchError("no_positive_training_ic_for_combination")
    scores = np.asarray(raw_scores, dtype=float)
    scores[~np.isfinite(scores)] = 0.0
    scores /= scores.sum()
    prior = np.full(scores.shape, 1.0 / len(scores), dtype=float)
    weights = (1.0 - policy.weight_shrinkage) * scores + policy.weight_shrinkage * prior
    previous = None
    if previous_weights is not None:
        previous = np.asarray(tuple(previous_weights), dtype=float)
        if previous.shape != weights.shape or not np.all(np.isfinite(previous)):
            raise PortfolioResearchError("previous_combination_weights_invalid")
        lower = np.maximum(previous - policy.max_weight_change, 0.0)
        upper = np.minimum(previous + policy.max_weight_change, policy.factor_weight_cap)
        weights = np.clip(weights, lower, upper)
    families = tuple(str(record["family"]) for record in factor_records)
    weights = project_group_caps(
        weights,
        families=families,
        clusters=tuple(clusters),
        factor_cap=policy.factor_weight_cap,
        family_cap=policy.family_weight_cap,
        cluster_cap=policy.cluster_weight_cap,
    )
    if previous is not None:
        maximum_change = float(np.max(np.abs(weights - previous)))
        if maximum_change > policy.max_weight_change:
            fraction = policy.max_weight_change / maximum_change
            weights = previous + fraction * (weights - previous)
            weights /= weights.sum()
    payload = {
        "factor_ids": [str(record["factor_id"]) for record in factor_records],
        "families": list(families),
        "cluster_ids": clusters,
        "weights": weights.tolist(),
        "mean_rank_ic": means,
        "icir": icirs,
        "residual_coefficients": coefficients.tolist(),
        "training_observations": observations,
        "train_indices": train.tolist(),
    }
    return CombinationFit(
        factor_ids=tuple(payload["factor_ids"]),
        families=families,
        cluster_ids=tuple(clusters),
        weights=tuple(float(value) for value in weights),
        mean_rank_ic=tuple(means),
        icir=tuple(icirs),
        residual_coefficients=tuple(tuple(float(value) for value in row) for row in coefficients),
        training_observations=tuple(observations),
        fit_hash=stable_hash(payload),
    )


def build_combined_signal(
    values: np.ndarray,
    validity: np.ndarray,
    common_cells: np.ndarray,
    fit: CombinationFit,
    *,
    min_breadth: int,
) -> tuple[np.ndarray, np.ndarray]:
    cube = np.asarray(values, dtype=float)
    masks = np.asarray(validity, dtype=bool)
    common = np.asarray(common_cells, dtype=bool)
    standardized, standardized_validity = standardize_factor_cube(
        cube,
        masks & common[None, :, :],
        min_breadth=min_breadth,
    )
    coefficients = np.asarray(fit.residual_coefficients, dtype=float)
    residual, residual_validity = apply_residualization(standardized, standardized_validity, coefficients)
    weights = np.asarray(fit.weights, dtype=float)[:, None, None]
    active_weights = weights * residual_validity
    denominator = active_weights.sum(axis=0)
    combined = np.zeros(cube.shape[1:], dtype=np.float32)
    valid = common & (denominator > 0.0)
    numerator = (residual * active_weights).sum(axis=0)
    combined[valid] = (numerator[valid] / denominator[valid]).astype(np.float32)
    combined[~valid] = 0.0
    return combined, valid


def standardize_factor_cube(
    values: np.ndarray,
    validity: np.ndarray,
    *,
    min_breadth: int,
) -> tuple[np.ndarray, np.ndarray]:
    cube = np.asarray(values, dtype=float)
    masks = np.asarray(validity, dtype=bool) & np.isfinite(cube)
    if cube.ndim != 3 or masks.shape != cube.shape:
        raise PortfolioResearchError("factor_cube_shape_invalid")
    output = np.zeros(cube.shape, dtype=float)
    output_validity = np.zeros(cube.shape, dtype=bool)
    for factor_index in range(cube.shape[0]):
        for date_index in range(cube.shape[1]):
            mask = masks[factor_index, date_index]
            if int(mask.sum()) < min_breadth:
                continue
            selected = cube[factor_index, date_index, mask]
            std = float(np.std(selected))
            if std <= 1e-12:
                continue
            output[factor_index, date_index, mask] = (selected - float(np.mean(selected))) / std
            output_validity[factor_index, date_index, mask] = True
    return output, output_validity


def factor_correlation_matrix(
    standardized: np.ndarray,
    validity: np.ndarray,
    train_indices: np.ndarray,
    *,
    min_observations: int,
) -> np.ndarray:
    count = standardized.shape[0]
    result = np.eye(count, dtype=float)
    for left in range(count):
        for right in range(left + 1, count):
            mask = validity[left, train_indices] & validity[right, train_indices]
            if int(mask.sum()) < min_observations:
                correlation = 0.0
            else:
                x = standardized[left, train_indices][mask]
                y = standardized[right, train_indices][mask]
                correlation = float(np.corrcoef(x, y)[0, 1])
                if not np.isfinite(correlation):
                    correlation = 0.0
            result[left, right] = result[right, left] = correlation
    return result


def correlation_clusters(correlations: np.ndarray, threshold: float) -> list[int]:
    matrix = np.asarray(correlations, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise PortfolioResearchError("factor_correlation_matrix_invalid")
    parent = list(range(matrix.shape[0]))

    def root(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    for left in range(matrix.shape[0]):
        for right in range(left + 1, matrix.shape[0]):
            if abs(float(matrix[left, right])) >= threshold:
                left_root = root(left)
                right_root = root(right)
                if left_root != right_root:
                    parent[right_root] = left_root
    labels: dict[int, int] = {}
    result = []
    for index in range(matrix.shape[0]):
        value = root(index)
        labels.setdefault(value, len(labels))
        result.append(labels[value])
    return result


def residualize_by_cluster(
    standardized: np.ndarray,
    validity: np.ndarray,
    train_indices: np.ndarray,
    clusters: Sequence[int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    count = standardized.shape[0]
    coefficients = np.zeros((count, count), dtype=float)
    for cluster in sorted(set(clusters)):
        members = [index for index, value in enumerate(clusters) if value == cluster]
        for position, target_index in enumerate(members[1:], start=1):
            predictors = members[:position]
            mask = validity[target_index, train_indices].copy()
            for predictor in predictors:
                mask &= validity[predictor, train_indices]
            if int(mask.sum()) <= len(predictors) + 2:
                continue
            design = np.stack([standardized[predictor, train_indices][mask] for predictor in predictors], axis=1)
            response = standardized[target_index, train_indices][mask]
            fitted, *_ = np.linalg.lstsq(design, response, rcond=None)
            for predictor, coefficient in zip(predictors, fitted):
                coefficients[target_index, predictor] = float(coefficient)
    residual, residual_validity = apply_residualization(standardized, validity, coefficients)
    return residual, residual_validity, coefficients


def apply_residualization(
    standardized: np.ndarray,
    validity: np.ndarray,
    coefficients: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    residual = np.asarray(standardized, dtype=float).copy()
    output_validity = np.asarray(validity, dtype=bool).copy()
    for target_index in range(residual.shape[0]):
        predictors = np.flatnonzero(np.abs(coefficients[target_index]) > 1e-15)
        for predictor in predictors:
            pair_valid = output_validity[target_index] & output_validity[predictor]
            output_validity[target_index] &= output_validity[predictor]
            residual[target_index, pair_valid] -= coefficients[target_index, predictor] * residual[predictor, pair_valid]
        residual[target_index, ~output_validity[target_index]] = 0.0
    return residual, output_validity


def project_group_caps(
    weights: np.ndarray,
    *,
    families: Sequence[str],
    clusters: Sequence[int],
    factor_cap: float,
    family_cap: float,
    cluster_cap: float,
) -> np.ndarray:
    result = np.asarray(weights, dtype=float).copy()
    if result.ndim != 1 or len(families) != result.size or len(clusters) != result.size:
        raise PortfolioResearchError("combination_weight_axis_invalid")
    if np.any(~np.isfinite(result)) or np.any(result < 0.0) or result.sum() <= 0.0:
        raise PortfolioResearchError("combination_weights_invalid")
    result /= result.sum()
    for _ in range(500):
        previous = result.copy()
        result = np.minimum(result, factor_cap)
        result = _cap_groups(result, families, family_cap)
        result = _cap_groups(result, clusters, cluster_cap)
        total = float(result.sum())
        if total <= 0.0:
            raise PortfolioResearchError("combination_group_caps_infeasible")
        result /= total
        if np.max(np.abs(result - previous)) <= 1e-12:
            break
    family_totals = _group_totals(result, families)
    cluster_totals = _group_totals(result, clusters)
    if (
        float(result.max()) > factor_cap + 1e-8
        or max(family_totals.values(), default=0.0) > family_cap + 1e-8
        or max(cluster_totals.values(), default=0.0) > cluster_cap + 1e-8
    ):
        raise PortfolioResearchError("combination_group_caps_infeasible")
    return result


def _cap_groups(weights: np.ndarray, labels: Sequence[Any], cap: float) -> np.ndarray:
    result = weights.copy()
    totals = _group_totals(result, labels)
    for label, total in totals.items():
        if total > cap:
            indices = [index for index, value in enumerate(labels) if value == label]
            result[indices] *= cap / total
    return result


def _group_totals(weights: np.ndarray, labels: Sequence[Any]) -> dict[Any, float]:
    totals: dict[Any, float] = {}
    for index, label in enumerate(labels):
        totals[label] = totals.get(label, 0.0) + float(weights[index])
    return totals


def _daily_rank_ic(
    factor: np.ndarray,
    target: np.ndarray,
    validity: np.ndarray,
    date_indices: np.ndarray,
    min_breadth: int,
) -> list[float]:
    result: list[float] = []
    for date_index in date_indices:
        mask = validity[date_index] & np.isfinite(factor[date_index]) & np.isfinite(target[date_index])
        if int(mask.sum()) < min_breadth:
            continue
        factor_rank = _research_combination_average_rank(factor[date_index, mask])
        target_rank = _research_combination_average_rank(target[date_index, mask])
        if float(np.std(factor_rank)) <= 1e-12 or float(np.std(target_rank)) <= 1e-12:
            continue
        value = float(np.corrcoef(factor_rank, target_rank)[0, 1])
        if np.isfinite(value):
            result.append(value)
    return result


def _research_combination_average_rank(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    order = np.argsort(array, kind="mergesort")
    ranks = np.empty(array.size, dtype=float)
    start = 0
    while start < array.size:
        end = start + 1
        while end < array.size and array[order[end]] == array[order[start]]:
            end += 1
        rank = (start + end - 1) / 2.0 + 1.0
        ranks[order[start:end]] = rank
        start = end
    return ranks


def _validate_inputs(values, validity, target, target_validity, common_cells, factor_records):
    cube = np.asarray(values, dtype=float)
    masks = np.asarray(validity, dtype=bool)
    target_values = np.asarray(target, dtype=float)
    target_mask = np.asarray(target_validity, dtype=bool)
    common = np.asarray(common_cells, dtype=bool)
    if cube.ndim != 3 or masks.shape != cube.shape:
        raise PortfolioResearchError("factor_cube_shape_invalid")
    if target_values.shape != cube.shape[1:] or target_mask.shape != target_values.shape or common.shape != target_values.shape:
        raise PortfolioResearchError("combination_target_or_common_shape_invalid")
    if cube.shape[0] != len(factor_records):
        raise PortfolioResearchError("factor_record_axis_mismatch")
    return cube, masks, target_values, target_mask, common

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import attach_artifact_metadata, write_jsonl_artifact



def publish_portfolio_research_result(result: dict[str, Any], output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root).resolve()
    generations = root / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    result_hash = str(result.get("content_hash") or stable_hash(result))
    generation_id = f"portfolio_research_{result_hash[:24]}"
    target = generations / generation_id
    if not target.exists():
        staging = Path(tempfile.mkdtemp(prefix=f".{generation_id}.", dir=generations))
        try:
            simulation_runs = list(result.get("simulation_runs") or [])
            report = {key: value for key, value in result.items() if key not in {"simulation_runs", "factor_weights", "windows"}}
            _write_artifact(staging / "portfolio_research_report.json", report, "portfolio_research_report")
            write_jsonl_artifact(
                staging / "portfolio_factor_weights.jsonl",
                result.get("factor_weights") or [],
                "portfolio_factor_weights",
                "portfolio_research",
            )
            write_jsonl_artifact(
                staging / "portfolio_walk_forward_windows.jsonl",
                result.get("windows") or [],
                "portfolio_walk_forward_windows",
                "portfolio_research",
            )
            simulation_catalog = []
            for run in simulation_runs:
                run_id = str((run.get("summary") or {}).get("run_id") or "")
                if not run_id:
                    raise PortfolioResearchError("portfolio_simulation_run_id_missing")
                safe_id = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:20]
                run_path = staging / "simulation_runs" / f"{safe_id}.json"
                _write_artifact(run_path, run, "portfolio_simulation_run")
                simulation_catalog.append(
                    {"run_id": run_id, "path": str(run_path.relative_to(staging)), "sha256": _research_report_sha256(run_path)}
                )
            _write_artifact(
                staging / "portfolio_simulation_catalog.json",
                {"run_count": len(simulation_catalog), "runs": simulation_catalog, "catalog_root": stable_hash(simulation_catalog)},
                "portfolio_simulation_catalog",
            )
            shadow_rows = []
            if result.get("status") == SHADOW_CANDIDATE_STATUS:
                shadow_rows.append(
                    {
                        "portfolio_research_content_hash": result_hash,
                        "status": "pending_independent_audit",
                        "shadow_only": True,
                        "paper_ready": False,
                        "live_ready": False,
                        "reason": "portfolio walk-forward passed; independent audit required before paper",
                    }
                )
            write_jsonl_artifact(
                staging / "portfolio_shadow_queue.jsonl",
                shadow_rows,
                "portfolio_shadow_queue",
                "portfolio_research",
            )
            manifest_core = {
                "status": result.get("status"),
                "result_content_hash": result_hash,
                "policy_id": result.get("policy_id"),
                "policy_hash": result.get("policy_hash"),
                "factor_certified_count": int(result.get("factor_certified_count") or 0),
                "walk_forward_window_count": int(result.get("walk_forward_window_count") or 0),
                "simulation_run_count": len(simulation_catalog),
                "shadow_queue_count": len(shadow_rows),
                "shadow_only": True,
                "independent_audit_required_for_paper": True,
                "certification_ready": False,
                "portfolio_ready": False,
                "paper_ready": False,
                "live_ready": False,
            }
            manifest = {
                **manifest_core,
                "content_hash": stable_hash(manifest_core),
                "generation_id": generation_id,
            }
            _write_artifact(staging / "portfolio_research_manifest.json", manifest, "portfolio_research_manifest")
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    manifest_path = target / "portfolio_research_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _validate_result_generation(target, manifest)
    pointer = {
        "generation_id": generation_id,
        "content_hash": manifest["content_hash"],
        "manifest": f"generations/{generation_id}/portfolio_research_manifest.json",
        "status": manifest["status"],
    }
    temporary = root / ".current.tmp"
    temporary.write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, root / "current.json")
    return {**manifest, "manifest_path": str(manifest_path), "generation_dir": str(target)}


def _validate_result_generation(root: Path, manifest: dict[str, Any]) -> None:
    required = {
        "portfolio_research_report.json",
        "portfolio_factor_weights.jsonl",
        "portfolio_walk_forward_windows.jsonl",
        "portfolio_simulation_catalog.json",
        "portfolio_shadow_queue.jsonl",
        "portfolio_research_manifest.json",
    }
    observed = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if not required.issubset(observed):
        raise PortfolioResearchError("portfolio_research_generation_incomplete")
    core = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id", "artifact_type", "producer", "created_at", "schema_version", "artifact_metadata"}}
    if stable_hash(core) != manifest.get("content_hash"):
        raise PortfolioResearchError("portfolio_research_manifest_hash_invalid")
    shadow_rows = _read_jsonl(root / "portfolio_shadow_queue.jsonl")
    if manifest.get("status") == SHADOW_CANDIDATE_STATUS and len(shadow_rows) != 1:
        raise PortfolioResearchError("portfolio_shadow_queue_missing")
    if manifest.get("status") != SHADOW_CANDIDATE_STATUS and shadow_rows:
        raise PortfolioResearchError("blocked_or_rejected_portfolio_entered_shadow_queue")


def _write_artifact(path, payload, artifact_type):
    path.parent.mkdir(parents=True, exist_ok=True)
    value = attach_artifact_metadata(dict(payload), artifact_type, "portfolio_research")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _research_report_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

from dataclasses import asdict, dataclass

import numpy as np



@dataclass(frozen=True)
class PortfolioWalkForwardSplit:
    split_id: str
    train_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    embargo_indices: tuple[int, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def build_portfolio_splits(
    common_eligible_dates: np.ndarray,
    policy: PortfolioResearchPolicy,
    *,
    effective_embargo: int,
) -> list[PortfolioWalkForwardSplit]:
    eligible = np.asarray(common_eligible_dates, dtype=bool).reshape(-1)
    if effective_embargo < policy.label_horizon:
        raise PortfolioResearchError("portfolio_embargo_shorter_than_label_horizon")
    segments = _segments(eligible)
    required = policy.train_size + policy.validation_size + policy.test_size + 2 * effective_embargo
    splits: list[PortfolioWalkForwardSplit] = []
    for segment_id, (start, end) in enumerate(segments):
        if end - start < required:
            continue
        cursor = start
        ordinal = 0
        while cursor + required <= end:
            train_end = cursor + policy.train_size
            validation_start = train_end + effective_embargo
            validation_end = validation_start + policy.validation_size
            test_start = validation_end + effective_embargo
            test_end = test_start + policy.test_size
            split = PortfolioWalkForwardSplit(
                split_id=f"segment_{segment_id}_portfolio_wf_{ordinal}",
                train_indices=tuple(range(cursor, train_end)),
                validation_indices=tuple(range(validation_start, validation_end)),
                test_indices=tuple(range(test_start, test_end)),
                embargo_indices=tuple(range(train_end, validation_start))
                + tuple(range(validation_end, test_start)),
            )
            if len(split.test_indices) != policy.test_size:
                raise PortfolioResearchError("portfolio_walk_forward_test_size_mismatch")
            splits.append(split)
            cursor += policy.step_size
            ordinal += 1
    if len(splits) < policy.min_evaluable_windows:
        raise PortfolioResearchError("portfolio_walk_forward_windows_insufficient")
    return splits


def _segments(mask: np.ndarray) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(mask.tolist() + [False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            result.append((start, index))
            start = None
    return result

import math
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

import numpy as np

from auto_alpha.portfolio.simulator.ledger_policy import ScenarioPolicy
from auto_alpha.portfolio.simulator.ledger_simulator import EventLedgerSimulator
from auto_alpha.portfolio.simulator.ledger_simulator import SimulationDataBlocker



REQUIRED_MARKET_FIELDS = ("open", "close", "valuation_open", "valuation_close", "lagged_adv")
REQUIRED_MASK_FIELDS = (
    "signal_candidate",
    "membership",
    "active",
    "open_execution_known",
    "buyable_at_open",
    "sellable_at_open",
    "open_validity",
    "close_validity",
    "valuation_open_validity",
    "valuation_close_validity",
    "lagged_adv_validity",
)


@dataclass(frozen=True)
class PortfolioResearchData:
    trade_dates: tuple[str, ...]
    assets: tuple[str, ...]
    factor_records: tuple[dict[str, Any], ...]
    factor_values: np.ndarray
    factor_validity: np.ndarray
    target: np.ndarray
    target_available: np.ndarray
    market: Mapping[str, np.ndarray]
    masks: Mapping[str, np.ndarray]
    universes: Mapping[str, np.ndarray]
    benchmarks: Mapping[str, Mapping[str, np.ndarray]]
    regimes: Mapping[str, np.ndarray]
    corporate_actions: tuple[dict[str, Any], ...] = ()
    lineage: Mapping[str, Any] = field(default_factory=dict)


def evaluate_portfolio_research(
    data: PortfolioResearchData,
    policy: PortfolioResearchPolicy,
    *,
    fee_calculator: Any,
    allow_test_policy: bool = False,
) -> dict[str, Any]:
    try:
        if not allow_test_policy:
            validate_production_policy(policy)
        validated = _validate_data(data, policy)
        return _evaluate(validated, policy, fee_calculator)
    except Exception as exc:
        return _blocked_result(policy, exc)


def _evaluate(data: PortfolioResearchData, policy: PortfolioResearchPolicy, fee_calculator: Any) -> dict[str, Any]:
    if fee_calculator is None:
        raise PortfolioResearchError("external_fee_schedule_required")
    factor_records = list(data.factor_records)
    max_lookback = max(int(row["effective_lookback"]) for row in factor_records)
    embargo = policy.effective_embargo(max_lookback)
    all_windows: list[dict[str, Any]] = []
    all_weights: list[dict[str, Any]] = []
    simulation_runs: list[dict[str, Any]] = []
    universe_summaries: dict[str, dict[str, Any]] = {}
    previous_weights_by_universe: dict[str, tuple[float, ...]] = {}

    signal_base = _strict_mask_product(data.masks, ("signal_candidate", "membership", "active", "close_validity"))
    factor_count = (data.factor_validity & signal_base[None, :, :]).sum(axis=0)
    signal_base &= factor_count >= min(2, policy.min_factor_count)
    target_common = signal_base & data.target_available & np.isfinite(data.target)

    for universe_name in sorted(data.universes):
        universe_mask = np.asarray(data.universes[universe_name], dtype=bool)
        universe_signal = signal_base & universe_mask
        universe_evaluation = target_common & universe_mask
        eligible_dates = universe_evaluation.sum(axis=1) >= policy.min_cross_section_breadth
        splits = build_portfolio_splits(eligible_dates, policy, effective_embargo=embargo)
        universe_runs: list[dict[str, Any]] = []
        positive_baseline_windows = 0
        evaluable_baseline_windows = 0
        for split in splits:
            fit = fit_factor_combination(
                data.factor_values,
                data.factor_validity,
                data.target,
                data.target_available,
                universe_signal,
                split.train_indices,
                factor_records,
                policy,
                previous_weights=previous_weights_by_universe.get(universe_name),
            )
            previous_weights_by_universe[universe_name] = fit.weights
            combined, combined_validity = build_combined_signal(
                data.factor_values,
                data.factor_validity,
                universe_signal,
                fit,
                min_breadth=policy.min_cross_section_breadth,
            )
            test_indices = np.asarray(split.test_indices, dtype=int)
            test_evaluation = universe_evaluation[test_indices] & combined_validity[test_indices]
            valid_test_dates = test_evaluation.sum(axis=1) >= policy.min_cross_section_breadth
            valid_test_date_count = int(valid_test_dates.sum())
            if valid_test_date_count < policy.min_valid_test_dates:
                raise PortfolioResearchError(
                    f"portfolio_test_dates_insufficient:{universe_name}:{split.split_id}:{valid_test_date_count}"
                )
            validation_ic = _combined_rank_ic(
                combined,
                data.target,
                universe_evaluation & combined_validity,
                split.validation_indices,
                policy.min_cross_section_breadth,
            )
            test_ic = _combined_rank_ic(
                combined,
                data.target,
                universe_evaluation & combined_validity,
                split.test_indices,
                policy.min_cross_section_breadth,
            )
            weight_row = {
                "universe": universe_name,
                "split_id": split.split_id,
                "fit_hash": fit.fit_hash,
                "factor_ids": list(fit.factor_ids),
                "families": list(fit.families),
                "cluster_ids": list(fit.cluster_ids),
                "weights": list(fit.weights),
                "mean_rank_ic": list(fit.mean_rank_ic),
                "icir": list(fit.icir),
                "training_observations": list(fit.training_observations),
                "train_start": data.trade_dates[split.train_indices[0]],
                "train_end": data.trade_dates[split.train_indices[-1]],
                "validation_rank_ic": validation_ic,
                "test_rank_ic": test_ic,
            }
            all_weights.append(weight_row)
            scenario_rows: list[dict[str, Any]] = []
            for scenario in policy.required_scenarios:
                run = _simulate_window(
                    data,
                    policy,
                    fee_calculator,
                    universe_name,
                    split.split_id,
                    test_indices,
                    combined,
                    combined_validity & universe_signal,
                    scenario,
                )
                scenario_rows.append(run["summary"])
                simulation_runs.append(run)
                universe_runs.append(run["summary"])
                if scenario.scenario_id == "baseline":
                    evaluable_baseline_windows += 1
                    if run["summary"]["net_total_return"] > policy.min_cost_adjusted_return:
                        positive_baseline_windows += 1
            all_windows.append(
                {
                    "universe": universe_name,
                    "split_id": split.split_id,
                    "effective_embargo": embargo,
                    "valid_test_date_count": valid_test_date_count,
                    "validation_rank_ic": validation_ic,
                    "test_rank_ic": test_ic,
                    "scenarios": scenario_rows,
                }
            )
        universe_summaries[universe_name] = _summarize_universe(
            universe_name,
            universe_runs,
            positive_baseline_windows,
            evaluable_baseline_windows,
            policy,
        )

    gate = _portfolio_gate(universe_summaries, simulation_runs, policy)
    status = SHADOW_CANDIDATE_STATUS if gate["passed"] else PORTFOLIO_REJECTED_STATUS
    semantic = {
        "status": status,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "factor_ids": [row["factor_id"] for row in factor_records],
        "formula_hashes": [row["formula_hash"] for row in factor_records],
        "factor_certified_count": len(factor_records),
        "effective_embargo": embargo,
        "walk_forward_window_count": len(all_windows),
        "universe_count": len(data.universes),
        "benchmark_count": len(data.benchmarks),
        "scenario_count": len(policy.required_scenarios),
        "universe_summaries": universe_summaries,
        "gate": gate,
        "lineage": dict(data.lineage),
        "factor_weights": all_weights,
        "windows": all_windows,
        "simulation_runs": simulation_runs,
        "shadow_ready": status == SHADOW_CANDIDATE_STATUS,
        "independent_audit_required_for_paper": True,
        "paper_ready": False,
        "live_ready": False,
        "portfolio_ready": False,
        "certification_ready": False,
        "certification_supported": False,
        "direct_live_forbidden": True,
    }
    semantic["content_hash"] = stable_hash(semantic)
    return semantic


def _simulate_window(
    data: PortfolioResearchData,
    policy: PortfolioResearchPolicy,
    fee_calculator: Any,
    universe_name: str,
    split_id: str,
    indices: np.ndarray,
    combined: np.ndarray,
    combined_validity: np.ndarray,
    scenario,
) -> dict[str, Any]:
    dates = [data.trade_dates[index] for index in indices]
    regime = None
    if scenario.required_regime:
        regime = np.asarray(data.regimes[scenario.required_regime], dtype=bool)[indices]
        if int(regime.sum()) < 2:
            raise PortfolioResearchError(
                f"portfolio_required_regime_dates_insufficient:{scenario.required_regime}:{split_id}"
            )
    market = {
        "dates": dates,
        "assets": list(data.assets),
        "open": np.asarray(data.market["open"], dtype=float)[indices],
        "close": np.asarray(data.market["close"], dtype=float)[indices],
        "valuation_open": np.asarray(data.market["valuation_open"], dtype=float)[indices],
        "valuation_close": np.asarray(data.market["valuation_close"], dtype=float)[indices],
        "adv": np.asarray(data.market["lagged_adv"], dtype=float)[indices] * scenario.lagged_adv_multiplier,
        "valuation_open_method": np.asarray(data.market.get("valuation_open_method"), dtype=object)[indices],
        "valuation_open_source_date": np.asarray(data.market.get("valuation_open_source_date"), dtype=object)[indices],
        "valuation_open_evidence_id": np.asarray(data.market.get("valuation_open_evidence_id"), dtype=object)[indices],
        "valuation_open_stale_age": np.asarray(data.market.get("valuation_open_stale_age"), dtype=np.int32)[indices],
        "valuation_close_method": np.asarray(data.market.get("valuation_close_method"), dtype=object)[indices],
        "valuation_close_source_date": np.asarray(data.market.get("valuation_close_source_date"), dtype=object)[indices],
        "valuation_close_evidence_id": np.asarray(data.market.get("valuation_close_evidence_id"), dtype=object)[indices],
        "valuation_close_stale_age": np.asarray(data.market.get("valuation_close_stale_age"), dtype=np.int32)[indices],
    }
    buy = (
        np.asarray(data.masks["buyable_at_open"], dtype=bool)
        & np.asarray(data.masks["open_execution_known"], dtype=bool)
        & np.asarray(data.masks["open_validity"], dtype=bool)
    )[indices]
    sell = (
        np.asarray(data.masks["sellable_at_open"], dtype=bool)
        & np.asarray(data.masks["open_execution_known"], dtype=bool)
        & np.asarray(data.masks["open_validity"], dtype=bool)
    )[indices]
    select = combined_validity[indices]
    scenario_policy = ScenarioPolicy(
        name=scenario.scenario_id,
        initial_aum=policy.initial_aum,
        top_n=policy.top_n,
        max_weight=policy.max_stock_weight,
        lot_size=policy.lot_size,
        adv_participation=0.10,
        modeled_cost_multiplier=scenario.modeled_cost_multiplier,
    )
    simulator = EventLedgerSimulator(
        scenario_policy,
        fee_calculator=fee_calculator,
        require_external_fee_schedule=True,
        require_explicit_valuation_marks=True,
    )
    try:
        result = simulator.run(
            market,
            combined[indices],
            masks={"buy": buy, "sell": sell, "select": select},
            corporate_actions=_window_actions(data.corporate_actions, data.trade_dates, indices),
        )
    except SimulationDataBlocker as exc:
        raise PortfolioResearchError(f"portfolio_event_ledger_blocked:{universe_name}:{split_id}:{scenario.scenario_id}:{exc}") from exc
    returns = []
    return_dates = []
    for row in result.nav:
        if row.open_to_open_return is None:
            continue
        if regime is not None and not bool(regime[row.index]):
            continue
        returns.append(float(row.open_to_open_return))
        return_dates.append(row.date)
    if not returns:
        raise PortfolioResearchError(f"portfolio_oos_nav_returns_missing:{universe_name}:{split_id}:{scenario.scenario_id}")
    net_return = _compound(returns)
    nav_values = [float(row.open_post) for row in result.nav if regime is None or bool(regime[row.index])]
    max_drawdown = _max_drawdown(nav_values)
    total_cost = float(sum(fill.total_cost for fill in result.fills))
    avg_nav = float(np.mean(nav_values)) if nav_values else 0.0
    turnover = float(sum(fill.notional for fill in result.fills) / avg_nav) if avg_nav > 0.0 else 0.0
    capacity_rejections = sum(rejection.reason in {"capacity_zero", "insufficient_capacity"} for rejection in result.rejections)
    benchmark_metrics = {}
    for benchmark_name, benchmark in sorted(data.benchmarks.items()):
        benchmark_returns = np.asarray(benchmark["returns"], dtype=float)[indices]
        benchmark_validity = np.asarray(benchmark["validity"], dtype=bool)[indices]
        selected = []
        for local_index, date in enumerate(dates):
            if date not in return_dates:
                continue
            if benchmark_validity[local_index] and np.isfinite(benchmark_returns[local_index]):
                selected.append(float(benchmark_returns[local_index]))
        if len(selected) != len(returns):
            raise PortfolioResearchError(
                f"benchmark_oos_alignment_invalid:{benchmark_name}:{universe_name}:{split_id}:{scenario.scenario_id}"
            )
        benchmark_return = _compound(selected)
        benchmark_metrics[benchmark_name] = {
            "benchmark_total_return": benchmark_return,
            "active_total_return": net_return - benchmark_return,
            "observation_count": len(selected),
        }
    summary = {
        "run_id": f"{universe_name}:{split_id}:{scenario.scenario_id}",
        "universe": universe_name,
        "split_id": split_id,
        "scenario_id": scenario.scenario_id,
        "net_total_return": net_return,
        "max_drawdown": max_drawdown,
        "turnover": turnover,
        "total_cost": total_cost,
        "fill_count": len(result.fills),
        "rejection_count": len(result.rejections),
        "capacity_rejection_count": int(capacity_rejections),
        "return_observation_count": len(returns),
        "benchmark_metrics": benchmark_metrics,
        "modeled_cost_multiplier": scenario.modeled_cost_multiplier,
        "lagged_adv_multiplier": scenario.lagged_adv_multiplier,
        "required_regime": scenario.required_regime,
    }
    return {
        "summary": summary,
        "orders": [item.to_dict() for item in result.orders],
        "fills": [item.to_dict() for item in result.fills],
        "rejections": [item.to_dict() for item in result.rejections],
        "settlements": [item.to_dict() for item in result.settlements],
        "nav": [item.to_dict() for item in result.nav],
        "event_ledger": result.event_ledger,
        "run_hash": stable_hash(result.to_dict()),
    }


def _summarize_universe(name, runs, positive_baseline, evaluable_baseline, policy):
    scenarios: dict[str, list[dict[str, Any]]] = {}
    for row in runs:
        scenarios.setdefault(str(row["scenario_id"]), []).append(row)
    scenario_summary = {}
    for scenario_id, rows in scenarios.items():
        returns = [float(row["net_total_return"]) for row in rows]
        scenario_summary[scenario_id] = {
            "window_count": len(rows),
            "positive_window_ratio": sum(value > policy.min_cost_adjusted_return for value in returns) / len(returns),
            "compounded_net_return": _compound(returns),
            "worst_window_return": min(returns),
            "max_drawdown": max(float(row["max_drawdown"]) for row in rows),
        }
    return {
        "universe": name,
        "baseline_window_count": evaluable_baseline,
        "baseline_positive_window_ratio": positive_baseline / evaluable_baseline if evaluable_baseline else 0.0,
        "scenarios": scenario_summary,
    }


def _portfolio_gate(universe_summaries, runs, policy):
    reasons: list[str] = []
    universe_passes = []
    stress_passes = []
    benchmark_passes = []
    for universe_name, summary in universe_summaries.items():
        baseline = (summary.get("scenarios") or {}).get("baseline") or {}
        passed = bool(
            summary.get("baseline_positive_window_ratio", 0.0) >= policy.min_positive_window_ratio
            and baseline.get("compounded_net_return", -1.0) > policy.min_cost_adjusted_return
            and baseline.get("max_drawdown", 1.0) <= policy.max_drawdown
        )
        universe_passes.append(passed)
        if not passed:
            reasons.append(f"universe_robustness_failed:{universe_name}")
        for scenario_id, scenario in (summary.get("scenarios") or {}).items():
            if scenario_id == "baseline":
                continue
            scenario_passed = bool(
                scenario.get("compounded_net_return", -1.0) > policy.min_cost_adjusted_return
                and scenario.get("positive_window_ratio", 0.0) >= policy.min_positive_window_ratio
                and scenario.get("max_drawdown", 1.0) <= policy.max_drawdown
            )
            stress_passes.append(scenario_passed)
            if not scenario_passed:
                reasons.append(f"stress_robustness_failed:{universe_name}:{scenario_id}")
    for row in runs:
        if row["summary"]["scenario_id"] != "baseline":
            continue
        for benchmark_name, benchmark in row["summary"]["benchmark_metrics"].items():
            passed = float(benchmark["active_total_return"]) > policy.min_active_return
            benchmark_passes.append(passed)
            if not passed:
                reasons.append(f"benchmark_active_return_failed:{row['summary']['universe']}:{benchmark_name}:{row['summary']['split_id']}")
    universe_ratio = sum(universe_passes) / len(universe_passes) if universe_passes else 0.0
    stress_ratio = sum(stress_passes) / len(stress_passes) if stress_passes else 0.0
    benchmark_ratio = sum(benchmark_passes) / len(benchmark_passes) if benchmark_passes else 0.0
    if universe_ratio < policy.min_universe_pass_ratio:
        reasons.append("multi_universe_pass_ratio_below_policy")
    if stress_ratio < policy.min_stress_pass_ratio:
        reasons.append("stress_pass_ratio_below_policy")
    if benchmark_ratio < policy.min_benchmark_pass_ratio:
        reasons.append("multi_benchmark_pass_ratio_below_policy")
    return {
        "passed": not reasons,
        "reasons": sorted(set(reasons)),
        "universe_pass_ratio": universe_ratio,
        "stress_pass_ratio": stress_ratio,
        "benchmark_pass_ratio": benchmark_ratio,
        "shadow_only": True,
        "paper_requires_independent_audit": True,
        "live_forbidden": True,
    }


def _validate_data(data: PortfolioResearchData, policy: PortfolioResearchPolicy) -> PortfolioResearchData:
    records = validate_factor_certified_records(
        data.factor_records,
        min_factor_count=policy.min_factor_count,
        min_family_count=policy.min_family_count,
    )
    dates = tuple(str(value) for value in data.trade_dates)
    assets = tuple(str(value) for value in data.assets)
    if len(set(dates)) != len(dates) or list(dates) != sorted(dates) or len(set(assets)) != len(assets):
        raise PortfolioResearchError("portfolio_axes_invalid")
    shape = (len(dates), len(assets))
    factor_shape = (len(records), *shape)
    factor_values = np.asarray(data.factor_values, dtype=float)
    factor_validity = np.asarray(data.factor_validity, dtype=bool)
    if factor_values.shape != factor_shape or factor_validity.shape != factor_shape:
        raise PortfolioResearchError("portfolio_factor_axes_mismatch")
    if np.any(factor_values[~factor_validity] != 0.0):
        raise PortfolioResearchError("invalid_factor_cells_must_store_zero")
    target = _finite_matrix(data.target, shape, "target", allow_invalid=True)
    target_available = _bool_matrix(data.target_available, shape, "target_available")
    if np.any(target_available & ~np.isfinite(target)):
        raise PortfolioResearchError("target_available_contains_nonfinite_target")
    if np.any(~target_available & (target != 0.0)):
        raise PortfolioResearchError("unavailable_target_cells_must_store_zero")
    if np.any(target_available[-policy.label_horizon :]):
        raise PortfolioResearchError("target_tail_endpoint_unavailable_contract_violated")
    market = dict(data.market)
    for field in REQUIRED_MARKET_FIELDS:
        market[field] = _finite_matrix(market.get(field), shape, f"market:{field}", allow_invalid=True)
    for field in (
        "valuation_open_method",
        "valuation_open_source_date",
        "valuation_open_evidence_id",
        "valuation_close_method",
        "valuation_close_source_date",
        "valuation_close_evidence_id",
    ):
        raw = np.asarray(market.get(field), dtype=object)
        if raw.shape != shape or np.any(raw == ""):
            raise PortfolioResearchError(f"explicit_valuation_metadata_invalid:{field}")
        market[field] = raw
    for field in ("valuation_open_stale_age", "valuation_close_stale_age"):
        raw = np.asarray(market.get(field), dtype=np.int32)
        if raw.shape != shape or np.any(raw < 0):
            raise PortfolioResearchError(f"explicit_valuation_metadata_invalid:{field}")
        market[field] = raw
    masks = dict(data.masks)
    for field in REQUIRED_MASK_FIELDS:
        masks[field] = _bool_matrix(masks.get(field), shape, f"mask:{field}")
    validity_by_field = {
        "open": "open_validity",
        "close": "close_validity",
        "valuation_open": "valuation_open_validity",
        "valuation_close": "valuation_close_validity",
        "lagged_adv": "lagged_adv_validity",
    }
    for field, validity_name in validity_by_field.items():
        valid = masks[validity_name]
        values = market[field]
        if np.any(valid & (~np.isfinite(values) | (values <= 0.0))):
            raise PortfolioResearchError(f"market_valid_cell_invalid:{field}")
        if np.any(~valid & np.isfinite(values) & (values != 0.0)):
            raise PortfolioResearchError(f"market_invalid_cell_not_zero:{field}")
    universes = {name: _bool_matrix(value, shape, f"universe:{name}") for name, value in data.universes.items()}
    if len(universes) < 2:
        raise PortfolioResearchError("multi_universe_evidence_required")
    benchmarks = {}
    for name, raw in data.benchmarks.items():
        returns = np.asarray(raw.get("returns"), dtype=float).reshape(-1)
        validity = np.asarray(raw.get("validity"), dtype=bool).reshape(-1)
        if returns.shape != (len(dates),) or validity.shape != returns.shape:
            raise PortfolioResearchError(f"benchmark_axis_invalid:{name}")
        if np.any(validity & ~np.isfinite(returns)):
            raise PortfolioResearchError(f"benchmark_validity_invalid:{name}")
        benchmarks[str(name)] = {"returns": returns, "validity": validity}
    if len(benchmarks) < 2:
        raise PortfolioResearchError("multi_benchmark_evidence_required")
    regimes = {name: np.asarray(value, dtype=bool).reshape(-1) for name, value in data.regimes.items()}
    if "extreme_volatility" not in regimes or regimes["extreme_volatility"].shape != (len(dates),):
        raise PortfolioResearchError("extreme_volatility_regime_required")
    return replace(
        data,
        trade_dates=dates,
        assets=assets,
        factor_records=tuple(records),
        factor_values=factor_values,
        factor_validity=factor_validity,
        target=target,
        target_available=target_available,
        market=market,
        masks=masks,
        universes=universes,
        benchmarks=benchmarks,
        regimes=regimes,
    )


def _strict_mask_product(masks: Mapping[str, np.ndarray], fields: Sequence[str]) -> np.ndarray:
    result = np.ones_like(np.asarray(masks[fields[0]], dtype=bool))
    for field in fields:
        result &= np.asarray(masks[field], dtype=bool)
    return result


def _combined_rank_ic(factor, target, validity, indices, min_breadth):
    values = []
    for index in indices:
        mask = validity[index] & np.isfinite(factor[index]) & np.isfinite(target[index])
        if int(mask.sum()) < min_breadth:
            continue
        left = _research_engine_average_rank(factor[index, mask])
        right = _research_engine_average_rank(target[index, mask])
        if np.std(left) <= 1e-12 or np.std(right) <= 1e-12:
            continue
        value = float(np.corrcoef(left, right)[0, 1])
        if np.isfinite(value):
            values.append(value)
    return float(np.mean(values)) if values else None


def _research_engine_average_rank(values):
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    result = np.empty(values.size, dtype=float)
    start = 0
    while start < values.size:
        end = start + 1
        while end < values.size and values[order[end]] == values[order[start]]:
            end += 1
        result[order[start:end]] = (start + end - 1) / 2.0 + 1.0
        start = end
    return result


def _window_actions(actions, dates, indices):
    selected_dates = {dates[index] for index in indices}
    result = []
    for raw in actions:
        row = dict(raw)
        date = str(row.get("ex_date") or row.get("effective_date") or "")
        if not date and row.get("effective_index") is not None:
            absolute = int(row["effective_index"])
            if 0 <= absolute < len(dates):
                date = dates[absolute]
        if date not in selected_dates:
            continue
        row.pop("effective_index", None)
        row["ex_date"] = date
        pay_date = str(row.get("pay_date") or "")
        if pay_date and pay_date not in selected_dates:
            row["pay_date"] = date
        result.append(row)
    return result


def _finite_matrix(value, shape, name, *, allow_invalid=False):
    array = np.asarray(value, dtype=float)
    if array.shape != shape:
        raise PortfolioResearchError(f"{name}_shape_invalid")
    if not allow_invalid and (np.any(~np.isfinite(array)) or np.any(array <= 0.0)):
        raise PortfolioResearchError(f"{name}_value_invalid")
    return array


def _bool_matrix(value, shape, name):
    array = np.asarray(value)
    if array.shape != shape or array.dtype != np.bool_:
        raise PortfolioResearchError(f"{name}_must_be_explicit_bool")
    return array.astype(bool, copy=True)


def _compound(returns):
    value = 1.0
    for item in returns:
        if not math.isfinite(float(item)):
            raise PortfolioResearchError("portfolio_return_nonfinite")
        value *= 1.0 + float(item)
    return value - 1.0


def _max_drawdown(nav_values):
    peak = 0.0
    drawdown = 0.0
    for value in nav_values:
        if not math.isfinite(value) or value < 0.0:
            raise PortfolioResearchError("portfolio_nav_invalid")
        peak = max(peak, value)
        if peak > 0.0:
            drawdown = max(drawdown, 1.0 - value / peak)
    return drawdown


def _blocked_result(policy: PortfolioResearchPolicy, exc: Exception) -> dict[str, Any]:
    blocker = f"{type(exc).__name__}:{exc}"
    semantic = {
        "status": DATA_BLOCKED_STATUS,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "factor_ids": [],
        "formula_hashes": [],
        "factor_certified_count": 0,
        "effective_embargo": 0,
        "walk_forward_window_count": 0,
        "universe_count": 0,
        "benchmark_count": 0,
        "scenario_count": len(policy.required_scenarios),
        "universe_summaries": {},
        "gate": {"passed": False, "reasons": [blocker]},
        "lineage": {},
        "factor_weights": [],
        "windows": [],
        "simulation_runs": [],
        "blockers": [blocker],
        "shadow_ready": False,
        "independent_audit_required_for_paper": True,
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "certification_supported": False,
        "direct_live_forbidden": True,
    }
    semantic["content_hash"] = stable_hash(semantic)
    return semantic

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from auto_alpha.portfolio.simulator.fees import validate_fee_schedule_v2



BUNDLE_SCHEMA = "factor_certified_portfolio_research_bundle_v1"


def publish_portfolio_research_bundle(
    output_root: str | Path,
    data: PortfolioResearchData,
    *,
    fee_schedule_manifest: str | Path,
    source_lineage: Mapping[str, str],
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    root = Path(output_root).resolve()
    if root.is_symlink():
        raise PortfolioResearchError("portfolio_bundle_output_symlink_forbidden")
    fee = validate_fee_schedule_v2(
        fee_schedule_manifest,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    fee_path = Path(fee["manifest_path"]).resolve()
    staging_parent = root / "generations"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".portfolio_bundle.", dir=staging_parent))
    artifacts: dict[str, dict[str, Any]] = {}
    try:
        _write_json(staging / "trade_dates.json", list(data.trade_dates))
        _register(artifacts, staging, "trade_dates", staging / "trade_dates.json")
        _write_json(staging / "assets.json", list(data.assets))
        _register(artifacts, staging, "assets", staging / "assets.json")
        _write_json(staging / "factor_certified_records.json", list(data.factor_records))
        _register(artifacts, staging, "factor_records", staging / "factor_certified_records.json")
        _write_json(staging / "auto_alpha.data.pit.corporate_actions.json", list(data.corporate_actions))
        _register(artifacts, staging, "corporate_actions", staging / "auto_alpha.data.pit.corporate_actions.json")
        _write_array(staging, artifacts, "factor_values", data.factor_values)
        _write_array(staging, artifacts, "factor_validity", data.factor_validity)
        _write_array(staging, artifacts, "target", data.target)
        _write_array(staging, artifacts, "target_available", data.target_available)
        for name, value in sorted(data.market.items()):
            _write_array(staging, artifacts, f"market:{name}", value)
        for name, value in sorted(data.masks.items()):
            _write_array(staging, artifacts, f"mask:{name}", value)
        for name, value in sorted(data.universes.items()):
            _write_array(staging, artifacts, f"universe:{name}", value)
        for name, payload in sorted(data.benchmarks.items()):
            _write_array(staging, artifacts, f"benchmark:{name}:returns", payload["returns"])
            _write_array(staging, artifacts, f"benchmark:{name}:validity", payload["validity"])
        for name, value in sorted(data.regimes.items()):
            _write_array(staging, artifacts, f"regime:{name}", value)
        fee_target = staging / "fee_schedule"
        shutil.copytree(fee_path.parent, fee_target)
        fee_relative = fee_target / fee_path.name
        for file_path in sorted(candidate for candidate in fee_target.rglob("*") if candidate.is_file()):
            _register(artifacts, staging, f"fee:{file_path.relative_to(fee_target)}", file_path)
        source = dict(sorted((str(key), str(value)) for key, value in source_lineage.items()))
        if not source or any(len(value) != 64 for value in source.values()):
            raise PortfolioResearchError("portfolio_bundle_source_lineage_invalid")
        semantic = {
            "schema_version": BUNDLE_SCHEMA,
            "status": "ready",
            "source_lineage": source,
            "source_lineage_root": stable_hash(source),
            "factor_ids": [str(row["factor_id"]) for row in data.factor_records],
            "formula_hashes": [str(row["formula_hash"]) for row in data.factor_records],
            "stock_axis_hash": stable_hash(list(data.assets)),
            "date_axis_hash": stable_hash(list(data.trade_dates)),
            "factor_axis_hash": stable_hash([str(row["factor_id"]) for row in data.factor_records]),
            "universe_names": sorted(data.universes),
            "benchmark_names": sorted(data.benchmarks),
            "regime_names": sorted(data.regimes),
            "fee_schedule_relative_path": str(fee_relative.relative_to(staging)),
            "fee_schedule_content_hash": str(fee["content_hash"]),
            "artifacts": artifacts,
            "fallback_allowed": False,
            "factor_values_storage": "float32_npy",
            "factor_validity_storage": "bool_npy",
        }
        content_hash = stable_hash(semantic)
        generation_id = f"portfolio_bundle_{content_hash[:24]}"
        manifest = {**semantic, "content_hash": content_hash, "generation_id": generation_id}
        _write_json(staging / "portfolio_research_bundle_manifest.json", manifest)
        target = staging_parent / generation_id
        if target.exists():
            existing = validate_portfolio_research_bundle(target / "portfolio_research_bundle_manifest.json", allow_synthetic_test_fixture=allow_synthetic_test_fixture)
            if existing["content_hash"] != content_hash:
                raise PortfolioResearchError("portfolio_bundle_generation_collision")
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        pointer = {
            "generation_id": generation_id,
            "content_hash": content_hash,
            "manifest": f"generations/{generation_id}/portfolio_research_bundle_manifest.json",
        }
        _atomic_json(root / "current.json", pointer)
        return validate_portfolio_research_bundle(
            target / "portfolio_research_bundle_manifest.json",
            allow_synthetic_test_fixture=allow_synthetic_test_fixture,
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def validate_portfolio_research_bundle(
    manifest_path: str | Path,
    *,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    if path.is_symlink() or not path.is_file():
        raise PortfolioResearchError("portfolio_bundle_manifest_missing_or_symlink")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != BUNDLE_SCHEMA or manifest.get("status") != "ready":
        raise PortfolioResearchError("portfolio_bundle_schema_or_status_invalid")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if stable_hash(semantic) != manifest.get("content_hash"):
        raise PortfolioResearchError("portfolio_bundle_content_hash_mismatch")
    expected_generation = f"portfolio_bundle_{manifest['content_hash'][:24]}"
    if manifest.get("generation_id") != expected_generation or path.parent.name != expected_generation:
        raise PortfolioResearchError("portfolio_bundle_generation_identity_mismatch")
    if manifest.get("fallback_allowed") is not False:
        raise PortfolioResearchError("portfolio_bundle_fallback_forbidden")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise PortfolioResearchError("portfolio_bundle_artifact_catalog_missing")
    registered = set()
    for name, entry in artifacts.items():
        target = _contained(path.parent, entry.get("path"))
        registered.add(str(target.relative_to(path.parent)))
        if _research_bundle_sha256(target) != entry.get("sha256") or target.stat().st_size != entry.get("size_bytes"):
            raise PortfolioResearchError(f"portfolio_bundle_artifact_integrity_invalid:{name}")
        if target.suffix == ".npy":
            array = np.load(target, mmap_mode="r", allow_pickle=False)
            if list(array.shape) != entry.get("shape") or str(array.dtype) != entry.get("dtype"):
                raise PortfolioResearchError(f"portfolio_bundle_array_contract_invalid:{name}")
    observed = {
        str(candidate.relative_to(path.parent))
        for candidate in path.parent.rglob("*")
        if candidate.is_file() and candidate != path
    }
    if observed != registered:
        raise PortfolioResearchError("portfolio_bundle_unregistered_file_detected")
    fee_path = _contained(path.parent, manifest.get("fee_schedule_relative_path"))
    fee = validate_fee_schedule_v2(fee_path, allow_synthetic_test_fixture=allow_synthetic_test_fixture)
    if fee.get("content_hash") != manifest.get("fee_schedule_content_hash"):
        raise PortfolioResearchError("portfolio_bundle_fee_lineage_mismatch")
    if stable_hash(manifest.get("source_lineage")) != manifest.get("source_lineage_root"):
        raise PortfolioResearchError("portfolio_bundle_source_lineage_root_mismatch")
    return {**manifest, "manifest_path": str(path)}


def load_portfolio_research_bundle(
    manifest_path: str | Path,
    *,
    allow_synthetic_test_fixture: bool = False,
) -> tuple[PortfolioResearchData, Path, dict[str, Any]]:
    manifest = validate_portfolio_research_bundle(
        manifest_path,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    root = Path(manifest["manifest_path"]).parent
    artifacts = manifest["artifacts"]

    def array(name: str) -> np.ndarray:
        return np.load(root / artifacts[name]["path"], mmap_mode="r", allow_pickle=False)

    dates = tuple(_read_json(root / artifacts["trade_dates"]["path"]))
    assets = tuple(_read_json(root / artifacts["assets"]["path"]))
    factors = tuple(_read_json(root / artifacts["factor_records"]["path"]))
    actions = tuple(_read_json(root / artifacts["corporate_actions"]["path"]))
    market = {name.removeprefix("market:"): array(name) for name in artifacts if name.startswith("market:")}
    masks = {name.removeprefix("mask:"): array(name) for name in artifacts if name.startswith("mask:")}
    universes = {name.removeprefix("universe:"): array(name) for name in artifacts if name.startswith("universe:")}
    benchmark_names = manifest["benchmark_names"]
    benchmarks = {
        name: {
            "returns": array(f"benchmark:{name}:returns"),
            "validity": array(f"benchmark:{name}:validity"),
        }
        for name in benchmark_names
    }
    regimes = {name: array(f"regime:{name}") for name in manifest["regime_names"]}
    data = PortfolioResearchData(
        trade_dates=dates,
        assets=assets,
        factor_records=factors,
        factor_values=array("factor_values"),
        factor_validity=array("factor_validity"),
        target=array("target"),
        target_available=array("target_available"),
        market=market,
        masks=masks,
        universes=universes,
        benchmarks=benchmarks,
        regimes=regimes,
        corporate_actions=actions,
        lineage=manifest["source_lineage"] | {"bundle_content_hash": manifest["content_hash"]},
    )
    return data, root / manifest["fee_schedule_relative_path"], manifest


def _write_array(root, artifacts, name, value):
    safe = name.replace(":", "__") + ".npy"
    path = root / "arrays" / safe
    path.parent.mkdir(parents=True, exist_ok=True)
    array = np.asarray(value)
    if array.dtype == object:
        array = array.astype(str)
    if name == "factor_values":
        array = array.astype(np.float32)
    elif "valid" in name or name.startswith(("mask:", "universe:", "regime:")) or name == "target_available":
        array = array.astype(np.bool_)
    np.save(path, array, allow_pickle=False)
    _register(artifacts, root, name, path, array=array)


def _register(artifacts, root, name, path, *, array=None):
    entry = {
        "path": str(path.relative_to(root)),
        "sha256": _research_bundle_sha256(path),
        "size_bytes": path.stat().st_size,
    }
    if array is not None:
        entry.update({"shape": list(array.shape), "dtype": str(array.dtype)})
    artifacts[name] = entry


def _contained(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise PortfolioResearchError("portfolio_bundle_relative_path_invalid")
    candidate = (root / relative).resolve()
    if candidate == root or root not in candidate.parents or candidate.is_symlink() or not candidate.is_file():
        raise PortfolioResearchError("portfolio_bundle_artifact_containment_invalid")
    return candidate


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    _write_json(temporary, payload)
    os.replace(temporary, path)


def _research_bundle_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

__all__ = [
    "CombinationFit",
    "FACTOR_CERTIFIED_STATUS",
    "PortfolioResearchData",
    "PortfolioResearchError",
    "PortfolioResearchPolicy",
    "StressScenario",
    "build_combined_signal",
    "evaluate_portfolio_research",
    "fit_factor_combination",
    "validate_factor_certified_records",
]

import argparse
import json

from auto_alpha.portfolio.simulator.fees import FeeScheduleCalculator



def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run locked factor-certified portfolio walk-forward auto_alpha.research.search.studies.")
    parser.add_argument("--bundle-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--policy-id", default="factor_certified_portfolio_walk_forward_v1")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = PortfolioResearchPolicy(policy_id=args.policy_id)
        data, fee_path, bundle = load_portfolio_research_bundle(args.bundle_manifest)
        calculator = FeeScheduleCalculator(fee_path)
        result = evaluate_portfolio_research(data, policy, fee_calculator=calculator)
        published = publish_portfolio_research_result(result, args.output_dir)
        payload = {
            "status": result["status"],
            "bundle_content_hash": bundle["content_hash"],
            "policy_hash": policy.policy_hash,
            "factor_certified_count": int(result.get("factor_certified_count") or 0),
            "walk_forward_window_count": int(result.get("walk_forward_window_count") or 0),
            "shadow_ready": bool(result.get("shadow_ready")),
            "paper_ready": False,
            "live_ready": False,
            "paths": published,
        }
    except Exception as exc:
        payload = {"status": DATA_BLOCKED_STATUS, "error": f"{type(exc).__name__}:{exc}"}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 2 if result["status"] == DATA_BLOCKED_STATUS else 0


if __name__ == "__main__":
    raise SystemExit(main())
