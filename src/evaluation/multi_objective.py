"""Dimensionless multi-objective scoring primitives.

Raw return spreads, ICIR, turnover, and monotonicity have incompatible units.
This module keeps hard admission thresholds outside ranking and only combines
bounded or cohort-normalized objective values.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any, Iterable


@dataclass(frozen=True)
class ObjectiveSpec:
    name: str
    direction: int
    weight: float
    required: bool = True

    def __post_init__(self) -> None:
        if self.direction not in {-1, 1}:
            raise ValueError(f"objective direction must be -1 or 1: {self.name}")
        if not math.isfinite(float(self.weight)) or float(self.weight) <= 0:
            raise ValueError(f"objective weight must be positive and finite: {self.name}")


def bounded_factor_score(metrics: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """Return a unit-free single-factor diagnostic score.

    The raw top-bottom spread is deliberately excluded. It remains an output
    metric, but cannot dominate IC or turnover merely because returns use a
    different unit.
    """

    components = {
        "rank_ic_ir": math.tanh(_finite(metrics.get("rank_ic_ir"))),
        "rank_ic_t_stat": math.tanh(_finite(metrics.get("rank_ic_t_stat")) / 3.0),
        "rank_ic_positive_ratio": _unit_interval_to_signed(metrics.get("rank_ic_positive_ratio")),
        "monotonicity": max(-1.0, min(1.0, _finite(metrics.get("monotonicity")))),
        "coverage": _unit_interval_to_signed(metrics.get("coverage")),
        "turnover": 1.0 - 2.0 * max(0.0, min(1.0, _finite(metrics.get("turnover")))),
    }
    score = sum(components.values()) / len(components)
    return float(score), {name: float(value) for name, value in components.items()}


def normalize_objective_rows(
    rows: Iterable[dict[str, Any]],
    objectives: Iterable[ObjectiveSpec],
    *,
    id_field: str,
) -> tuple[dict[str, float], dict[str, dict[str, float]], dict[str, Any]]:
    """Normalize a candidate cohort with average-tie empirical percentiles."""

    records = list(rows)
    specs = tuple(objectives)
    identifiers = [str(row.get(id_field) or "") for row in records]
    if any(not value for value in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError(f"{id_field} must be non-empty and unique")
    normalized_by_metric: dict[str, dict[str, float]] = {}
    reference: dict[str, Any] = {
        "method": "empirical_cdf_average_ties_v1",
        "candidate_count": len(records),
        "objectives": [asdict(spec) for spec in specs],
        "metrics": {},
    }
    missing_required: dict[str, list[str]] = {identifier: [] for identifier in identifiers}
    for spec in specs:
        finite_values: list[tuple[str, float]] = []
        for identifier, row in zip(identifiers, records):
            value = _optional_finite(row.get(spec.name))
            if value is None:
                if spec.required:
                    missing_required[identifier].append(spec.name)
                continue
            finite_values.append((identifier, value))
        normalized = _average_tie_percentiles(finite_values, direction=spec.direction)
        normalized_by_metric[spec.name] = normalized
        values = [value for _, value in finite_values]
        reference["metrics"][spec.name] = {
            "count": len(values),
            "min": min(values) if values else None,
            "median": median(values) if values else None,
            "max": max(values) if values else None,
            "direction": spec.direction,
            "weight": float(spec.weight),
        }
    scores: dict[str, float] = {}
    components: dict[str, dict[str, float]] = {}
    total_weight = sum(float(spec.weight) for spec in specs)
    for identifier in identifiers:
        candidate_components: dict[str, float] = {}
        if missing_required[identifier]:
            scores[identifier] = float("nan")
            components[identifier] = candidate_components
            continue
        weighted = 0.0
        for spec in specs:
            value = normalized_by_metric[spec.name][identifier]
            candidate_components[spec.name] = float(value)
            weighted += float(spec.weight) * float(value)
        scores[identifier] = float(weighted / total_weight)
        components[identifier] = candidate_components
    reference["missing_required"] = {key: value for key, value in missing_required.items() if value}
    reference["reference_hash"] = hashlib.sha256(
        json.dumps(reference, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return scores, components, reference


def _average_tie_percentiles(values: list[tuple[str, float]], *, direction: int) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values, key=lambda item: (item[1], item[0]))
    denominator = max(len(ordered) - 1, 1)
    result: dict[str, float] = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average_rank = (start + end - 1) / 2.0
        signed = (2.0 * average_rank / denominator - 1.0) if len(ordered) > 1 else 0.0
        for identifier, _ in ordered[start:end]:
            result[identifier] = float(direction * signed)
        start = end
    return result


def _unit_interval_to_signed(value: Any) -> float:
    return 2.0 * max(0.0, min(1.0, _finite(value))) - 1.0


def _optional_finite(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _finite(value: Any) -> float:
    numeric = _optional_finite(value)
    return numeric if numeric is not None else 0.0
