"""Correlation-aware, train-only factor combination."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

import numpy as np

from auto_alpha.portfolio.construction.research_contracts import PortfolioResearchError
from auto_alpha.portfolio.construction.research_contracts import PortfolioResearchPolicy
from auto_alpha.portfolio.construction.research_contracts import stable_hash


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
        factor_rank = _average_rank(factor[date_index, mask])
        target_rank = _average_rank(target[date_index, mask])
        if float(np.std(factor_rank)) <= 1e-12 or float(np.std(target_rank)) <= 1e-12:
            continue
        value = float(np.corrcoef(factor_rank, target_rank)[0, 1])
        if np.isfinite(value):
            result.append(value)
    return result


def _average_rank(values: np.ndarray) -> np.ndarray:
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
