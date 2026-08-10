"""Canonical split, metric, scoring, and report utilities for factor research."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import torch


@dataclass(frozen=True)
class TimeSeriesSplitResult:
    train_dates: list[str]
    valid_dates: list[str]
    test_dates: list[str]
    embargo_dates: list[str]


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


@dataclass(frozen=True)
class FactorReport:
    factor_id: str
    experiment_id: str
    formula: list[str]
    formula_tokens: list[int]
    metrics_by_split: dict[str, dict[str, float]]
    n_stocks: int
    n_dates: int
    n_features: int
    train_dates: list[str]
    valid_dates: list[str]
    test_dates: list[str]
    created_at: str
    transform_method: str | None = None
    gate_decision: dict[str, object] | None = None
    max_abs_correlation: float | None = None
    similar_factors: list[dict[str, object]] | None = None
    status: str | None = None


def split_trade_dates(
    trade_dates: list[str],
    train_ratio: float = 0.6,
    valid_ratio: float = 0.2,
    embargo_size: int = 0,
) -> TimeSeriesSplitResult:
    dates = sorted(trade_dates)
    count = len(dates)
    if count == 0:
        return TimeSeriesSplitResult([], [], [], [])
    if count == 1:
        return TimeSeriesSplitResult([], [], dates, [])
    if count == 2:
        return TimeSeriesSplitResult(dates[:1], [], dates[1:], [])
    train_count = min(max(1, int(count * train_ratio)), count - 2)
    remaining = count - train_count
    valid_count = min(max(1, int(count * valid_ratio)), remaining - 1)
    train_end = train_count
    valid_end = train_count + valid_count
    embargo = max(0, int(embargo_size))
    valid_start = min(train_end + embargo, valid_end)
    test_start = min(valid_end + embargo, count)
    return TimeSeriesSplitResult(
        train_dates=dates[:train_end],
        valid_dates=dates[valid_start:valid_end],
        test_dates=dates[test_start:],
        embargo_dates=dates[train_end:valid_start] + dates[valid_end:test_start],
    )


def evaluate_by_date_mask(
    evaluator,
    factors: torch.Tensor,
    raw_data: dict[str, torch.Tensor],
    target_ret: torch.Tensor,
    trade_dates: list[str],
    selected_dates: list[str],
) -> dict[str, float]:
    selected = set(selected_dates)
    indices = [index for index, trade_date in enumerate(trade_dates) if trade_date in selected]
    if indices:
        index_tensor = torch.tensor(indices, dtype=torch.long, device=factors.device)
        split_factors = factors.index_select(1, index_tensor)
        split_target = target_ret.index_select(1, index_tensor)
        split_raw = {key: _select_dates(value, index_tensor) for key, value in raw_data.items()}
    else:
        split_factors = factors[:, :0]
        split_target = target_ret[:, :0]
        split_raw = {key: _empty_dates(value) for key, value in raw_data.items()}
    metrics = evaluator.evaluate(split_factors, split_raw, split_target).to_dict()
    return {key: float(value) for key, value in metrics.items()}


def evaluate_by_splits(
    evaluator,
    factors: torch.Tensor,
    raw_data: dict[str, torch.Tensor],
    target_ret: torch.Tensor,
    trade_dates: list[str],
    split_result: TimeSeriesSplitResult,
) -> dict[str, dict[str, float]]:
    return {
        "train": evaluate_by_date_mask(evaluator, factors, raw_data, target_ret, trade_dates, split_result.train_dates),
        "valid": evaluate_by_date_mask(evaluator, factors, raw_data, target_ret, trade_dates, split_result.valid_dates),
        "test": evaluate_by_date_mask(evaluator, factors, raw_data, target_ret, trade_dates, split_result.test_dates),
        "all": evaluate_by_date_mask(evaluator, factors, raw_data, target_ret, trade_dates, trade_dates),
    }


def bounded_factor_score(metrics: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """Combine only bounded, dimensionless diagnostics; raw spread is excluded."""

    components = {
        "rank_ic_ir": math.tanh(_finite(metrics.get("rank_ic_ir"))),
        "rank_ic_t_stat": math.tanh(_finite(metrics.get("rank_ic_t_stat")) / 3.0),
        "rank_ic_positive_ratio": _unit_interval_to_signed(metrics.get("rank_ic_positive_ratio")),
        "monotonicity": max(-1.0, min(1.0, _finite(metrics.get("monotonicity")))),
        "coverage": _unit_interval_to_signed(metrics.get("coverage")),
        "turnover": 1.0 - 2.0 * max(0.0, min(1.0, _finite(metrics.get("turnover")))),
    }
    return float(sum(components.values()) / len(components)), components


def normalize_objective_rows(
    rows: Iterable[dict[str, Any]],
    objectives: Iterable[ObjectiveSpec],
    *,
    id_field: str,
) -> tuple[dict[str, float], dict[str, dict[str, float]], dict[str, Any]]:
    records = list(rows)
    specs = tuple(objectives)
    identifiers = [str(row.get(id_field) or "") for row in records]
    if any(not identifier for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
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
        normalized_by_metric[spec.name] = _average_tie_percentiles(finite_values, direction=spec.direction)
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
        if missing_required[identifier]:
            scores[identifier] = float("nan")
            components[identifier] = {}
            continue
        candidate_components = {
            spec.name: float(normalized_by_metric[spec.name][identifier])
            for spec in specs
        }
        scores[identifier] = float(
            sum(float(spec.weight) * candidate_components[spec.name] for spec in specs) / total_weight
        )
        components[identifier] = candidate_components
    reference["missing_required"] = {key: value for key, value in missing_required.items() if value}
    reference["reference_hash"] = hashlib.sha256(
        json.dumps(reference, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return scores, components, reference


def build_factor_report(
    factor_id: str,
    experiment_id: str,
    formula: list[str],
    formula_tokens: list[int],
    metrics_by_split: dict[str, dict[str, float]],
    n_stocks: int,
    n_dates: int,
    n_features: int,
    train_dates: list[str],
    valid_dates: list[str],
    test_dates: list[str],
    created_at: str,
    transform_method: str | None = None,
    gate_decision: dict[str, object] | None = None,
    max_abs_correlation: float | None = None,
    similar_factors: list[dict[str, object]] | None = None,
    status: str | None = None,
) -> FactorReport:
    return FactorReport(
        factor_id=factor_id,
        experiment_id=experiment_id,
        formula=formula,
        formula_tokens=formula_tokens,
        metrics_by_split=metrics_by_split,
        n_stocks=n_stocks,
        n_dates=n_dates,
        n_features=n_features,
        train_dates=train_dates,
        valid_dates=valid_dates,
        test_dates=test_dates,
        created_at=created_at,
        transform_method=transform_method,
        gate_decision=gate_decision,
        max_abs_correlation=max_abs_correlation,
        similar_factors=similar_factors,
        status=status,
    )


def write_factor_report(report: FactorReport, output_dir: str | Path) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "factor_report.json"
    markdown_path = output_path / "factor_report.md"
    json_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_factor_report(report), encoding="utf-8")
    return json_path, markdown_path


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


def _render_factor_report(report: FactorReport) -> str:
    lines = [
        "# Factor Report",
        "",
        f"- factor_id: `{report.factor_id}`",
        f"- experiment_id: `{report.experiment_id}`",
        f"- formula: `{' '.join(report.formula)}`",
        f"- created_at: `{report.created_at}`",
        f"- status: `{report.status or 'candidate'}`",
        f"- transform_method: `{report.transform_method or 'raw'}`",
        f"- max_abs_correlation: `{float(report.max_abs_correlation or 0.0):.6f}`",
        "",
        "## Sample Ranges",
        "",
        f"- train: `{_date_range(report.train_dates)}`",
        f"- valid: `{_date_range(report.valid_dates)}`",
        f"- test: `{_date_range(report.test_dates)}`",
        "",
        "## Metrics",
        "",
    ]
    metric_names = _metric_names(report.metrics_by_split)
    lines.append("| split | " + " | ".join(metric_names) + " |")
    lines.append("| --- | " + " | ".join("---:" for _ in metric_names) + " |")
    for split_name in ("train", "valid", "test", "all"):
        metrics = report.metrics_by_split.get(split_name, {})
        values = [f"{float(metrics.get(name, 0.0)):.6f}" for name in metric_names]
        lines.append("| " + split_name + " | " + " | ".join(values) + " |")
    if report.gate_decision is not None or report.similar_factors:
        lines.extend(["", "## Gate And Correlation", ""])
        if report.gate_decision is not None:
            lines.extend(["```json", json.dumps(report.gate_decision, ensure_ascii=False, indent=2), "```"])
        lines.append(f"- similar_factors: `{len(report.similar_factors or [])}`")
    return "\n".join(lines) + "\n"


def _metric_names(metrics_by_split: dict[str, dict[str, float]]) -> list[str]:
    preferred = [
        "rank_ic_mean",
        "rank_ic_std",
        "rank_ic_ir",
        "rank_ic_t_stat",
        "rank_ic_positive_ratio",
        "top_bottom_spread",
        "top_bottom_win_rate",
        "monotonicity",
        "coverage",
        "turnover",
        "score",
    ]
    present = {key for split_metrics in metrics_by_split.values() for key in split_metrics}
    ordered = [name for name in preferred if name in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered or ["score"]


def _select_dates(value, index_tensor: torch.Tensor):
    if isinstance(value, torch.Tensor) and value.ndim >= 2 and value.shape[1] >= int(index_tensor.max().item()) + 1:
        return value.index_select(1, index_tensor.to(device=value.device))
    return value


def _empty_dates(value):
    return value[:, :0] if isinstance(value, torch.Tensor) and value.ndim >= 2 else value


def _date_range(dates: list[str]) -> str:
    return "N/A" if not dates else f"{dates[0]} - {dates[-1]}"


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


__all__ = [
    "FactorReport",
    "ObjectiveSpec",
    "TimeSeriesSplitResult",
    "bounded_factor_score",
    "build_factor_report",
    "evaluate_by_date_mask",
    "evaluate_by_splits",
    "normalize_objective_rows",
    "split_trade_dates",
    "write_factor_report",
]
