"""Research proxy and full evaluation, scoring, holdout firewall, and trial ledger."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import torch

from auto_alpha.research.search.models import ObjectiveSpec


@dataclass(frozen=True)
class TimeSeriesSplitResult:
    train_dates: list[str]
    valid_dates: list[str]
    test_dates: list[str]
    embargo_dates: list[str]


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

import json
from pathlib import Path
from typing import Any


_PATH_FIELDS = {
    "data_dir",
    "output_dir",
    "factor_store_dir",
    "report_dir",
    "candidates_json",
    "universe_file",
}
_SENTINEL = "holdout_feedback_forbidden.json"


def assert_no_holdout_feedback_paths(config: Any) -> None:
    """Fail before output creation when any configured path descends from holdout evidence."""

    values = config.to_dict() if hasattr(config, "to_dict") else dict(config)
    for field, raw_value in values.items():
        if field not in _PATH_FIELDS and not field.endswith(("_path", "_dir", "_dirs")):
            continue
        candidates = raw_value if isinstance(raw_value, list) else [raw_value]
        for candidate in candidates:
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            path = Path(candidate).expanduser().resolve(strict=False)
            for ancestor in (path, *path.parents):
                sentinel = ancestor / _SENTINEL
                if not sentinel.exists():
                    continue
                if sentinel.is_symlink() or not sentinel.is_file():
                    raise RuntimeError(f"sealed_holdout_feedback_sentinel_invalid:{field}")
                try:
                    payload = json.loads(sentinel.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise RuntimeError(f"sealed_holdout_feedback_sentinel_invalid:{field}") from exc
                if (
                    payload.get("artifact_type") != "holdout_feedback_firewall"
                    or payload.get("feedback_to_search_forbidden") is not True
                    or payload.get("search_agent_readable") is not False
                ):
                    raise RuntimeError(f"sealed_holdout_feedback_sentinel_invalid:{field}")
                raise RuntimeError(f"sealed_holdout_feedback_path_forbidden:{field}")

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

import torch

from auto_alpha.research.factors.engine import preprocess_factor_with_validity
from auto_alpha.research.search.models import AlphaResearchPolicy
from auto_alpha.research.search.models import load_alpha_research_policy
from auto_alpha.research.formulas.vm import StackVM
from auto_alpha.validation.firewall.core_lineage import build_loader_lineage


def run_proxy_eval(
    candidates,
    loader,
    *,
    max_candidates: int,
    max_dates: int,
    vocab=None,
    seed: int = 0,
    policy: AlphaResearchPolicy | None = None,
    family_novelty_scores: dict[str, float] | None = None,
    existing_factor_matrices: list[torch.Tensor] | None = None,
    proxy_context_hash: str | None = None,
) -> tuple[list, list[dict], dict]:
    policy = policy or load_alpha_research_policy("alpha_factory_two_stage_smoke_v1")
    family_novelty_scores = family_novelty_scores or {}
    existing_factor_matrices = existing_factor_matrices or []
    vm = StackVM(vocab)
    candidates_by_id = {item.alpha_candidate_id: item for item in candidates}
    unchanged = []
    rows: list[dict] = []
    attempted = 0
    research_indices, eligible_date_hash = _loader_research_indices(loader)
    date_count = min(max_dates, len(research_indices))
    if date_count <= 0:
        raise RuntimeError("proxy has no eligible research dates")
    if date_count == 1:
        date_indices = [0]
    else:
        positions = [(idx * (len(research_indices) - 1)) // (date_count - 1) for idx in range(date_count)]
        offset = int(seed) % len(research_indices)
        date_indices = sorted({research_indices[(position + offset) % len(research_indices)] for position in positions})
    if date_count == 1:
        date_indices = [research_indices[0]]
    date_tensor = torch.tensor(date_indices, dtype=torch.long, device=loader.feat_tensor.device)
    lineage = build_loader_lineage(
        loader,
        stage="alpha_proxy_eval",
        extra={
            "max_dates": int(max_dates),
            "seed": int(seed),
            "research_policy_hash": policy.policy_hash,
            "existing_factor_count": len(existing_factor_matrices),
            "proxy_context_hash": proxy_context_hash,
        },
    )
    _audit_sampled_target_reads(loader, date_indices)
    for candidate in candidates:
        if candidate.status == "rejected" or attempted >= max_candidates:
            unchanged.append(candidate)
            continue
        attempted += 1
        start = time.perf_counter()
        try:
            feature_validity = _loader_feature_validity(loader)
            executed = vm.execute_with_validity(candidate.formula_tokens, loader.feat_tensor, feature_validity)
            if executed is None:
                raise RuntimeError("vm returned no factor")
            factor, factor_validity = executed
            factor = factor.index_select(1, date_tensor)
            factor_validity = factor_validity.index_select(1, date_tensor)
            target = loader.target_ret.index_select(1, date_tensor)
            target_available = _loader_target_available(loader).index_select(1, date_tensor)
            eligible = _loader_signal_eligibility(loader).index_select(1, date_tensor)
            neutralized, neutralized_validity, neutralization_status = _neutralize_proxy_factor(
                factor,
                factor_validity,
                loader,
                date_tensor,
                eligible,
                policy,
            )
            metric_validity = neutralized_validity & target_available & torch.isfinite(target) & eligible
            denominator = int(eligible.sum().item())
            valid_count = int((neutralized_validity & eligible).sum().item())
            coverage = float(valid_count / denominator) if denominator else 0.0
            valid_values = neutralized[neutralized_validity & eligible]
            std = float(valid_values.std(unbiased=False).item()) if valid_values.numel() else 0.0
            nonzero = float((valid_values != 0).to(torch.float32).mean().item()) if valid_values.numel() else 0.0
            missing = float(1.0 - coverage)
            ic_values = _daily_rank_ics(neutralized, target, metric_validity, policy.proxy_min_cross_section_breadth)
            rank_ic = float(sum(ic_values) / len(ic_values)) if ic_values else 0.0
            rank_ic_std = float(torch.tensor(ic_values).std(unbiased=False).item()) if len(ic_values) > 1 else 0.0
            hit_rate = float(sum(value > 0 for value in ic_values) / len(ic_values)) if ic_values else 0.0
            ic_stability = float(hit_rate / (1.0 + rank_ic_std))
            turnover = _turnover_proxy(neutralized, neutralized_validity & eligible)
            max_corr = _max_existing_correlation(
                neutralized,
                neutralized_validity & eligible,
                existing_factor_matrices,
                date_tensor,
            )
            universe_metrics = _universe_direction_metrics(
                neutralized,
                target,
                metric_validity,
                loader,
                policy.proxy_min_cross_section_breadth,
            )
            blockers = []
            if coverage < policy.proxy_min_coverage:
                blockers.append("proxy_coverage_below_policy")
            if std <= 1e-8:
                blockers.append("proxy_zero_variance")
            if len(ic_values) < policy.proxy_min_evaluable_dates:
                blockers.append("proxy_insufficient_evaluable_dates")
            if universe_metrics["evaluable_universe_count"] < policy.proxy_min_universe_count:
                blockers.append("proxy_insufficient_universe_evidence")
            if max_corr > policy.proxy_max_abs_existing_correlation:
                blockers.append("proxy_existing_factor_correlation_above_policy")
            if neutralization_status != "applied" and getattr(loader, "production_research", False):
                blockers.append("proxy_pit_neutralization_unavailable")
            row = {
                "alpha_candidate_id": candidate.alpha_candidate_id,
                "formula_hash": candidate.formula_hash,
                "status": "proxy_evaluated",
                "pit_safe": bool(getattr(loader, "production_research", False) or neutralization_status in {"applied", "nonproduction_fallback"}),
                "semantic_valid": str(getattr(candidate, "static_check_status", "passed")) == "passed",
                "neutralization_method": policy.proxy_neutralization,
                "neutralization_status": neutralization_status,
                "coverage": coverage,
                "cross_sectional_std": std,
                "nonzero_ratio": nonzero,
                "missing_value_ratio": missing,
                "preliminary_rank_ic": rank_ic,
                "neutralized_rank_ic_mean": rank_ic,
                "neutralized_rank_ic_std": rank_ic_std,
                "rank_ic_positive_ratio": hit_rate,
                "rank_ic_evaluable_dates": len(ic_values),
                "ic_stability": ic_stability,
                "turnover_proxy": turnover,
                "max_abs_existing_correlation": max_corr,
                "family_novelty": float(family_novelty_scores.get(candidate.alpha_candidate_id, 0.5)),
                "universe_direction_consistency": universe_metrics["direction_consistency"],
                "universe_rank_ic": universe_metrics["rank_ic_by_universe"],
                "evaluable_universe_count": universe_metrics["evaluable_universe_count"],
                "complexity": int(getattr(candidate, "complexity", 0) or 0),
                "lookback": int(getattr(candidate, "lookback", 0) or 0),
                "proxy_blockers": blockers,
                "runtime_ms": float((time.perf_counter() - start) * 1000.0),
                "proxy_score": None,
                "sampled_dates": [loader.trade_dates[index] for index in date_indices],
                "lineage_hash": lineage["lineage_hash"],
                "research_policy_id": policy.policy_id,
                "research_policy_hash": policy.policy_hash,
            }
            rows.append(row)
        except Exception as exc:
            rows.append(
                {
                    "alpha_candidate_id": candidate.alpha_candidate_id,
                    "formula_hash": candidate.formula_hash,
                    "status": "failed",
                    "error": str(exc),
                    "proxy_score": 0.0,
                    "runtime_ms": float((time.perf_counter() - start) * 1000.0),
                    "proxy_blockers": [f"proxy_eval_failed:{type(exc).__name__}"],
                }
            )
    scoreable = [row for row in rows if row.get("status") == "proxy_evaluated"]
    scores, components, normalization = normalize_objective_rows(
        scoreable,
        policy.proxy_objectives,
        id_field="alpha_candidate_id",
    )
    rows_by_id = {str(row["alpha_candidate_id"]): row for row in rows}
    passed = 0
    updated = []
    for candidate in candidates:
        row = rows_by_id.get(candidate.alpha_candidate_id)
        if row is None:
            updated.append(candidate)
            continue
        if row.get("status") == "failed":
            updated.append(replace(candidate, status="rejected", reject_reason=str((row.get("proxy_blockers") or ["proxy_eval_failed"])[0])))
            continue
        score = scores.get(candidate.alpha_candidate_id, float("nan"))
        blockers = list(row.get("proxy_blockers") or [])
        if not torch.isfinite(torch.tensor(score)):
            blockers.append("proxy_objective_missing")
            score = 0.0
        status = "proxy_passed" if not blockers else "rejected"
        row.update(
            {
                "status": status,
                "proxy_score": float(score),
                "normalized_objectives": components.get(candidate.alpha_candidate_id, {}),
                "normalization_reference_hash": normalization["reference_hash"],
                "proxy_blockers": blockers,
            }
        )
        if status == "proxy_passed":
            passed += 1
        updated.append(
            replace(
                candidate,
                proxy_score=float(score),
                status=status,
                reject_reason=None if status == "proxy_passed" else ";".join(blockers),
            )
        )
    summary = {
        "attempted": attempted,
        "passed": passed,
        "failed": sum(1 for row in rows if row.get("status") == "failed"),
        "max_dates": date_count,
        "sampled_dates": [loader.trade_dates[index] for index in date_indices],
        "eligible_date_hash": eligible_date_hash,
        "seed": int(seed),
        "lineage": lineage,
        "lineage_hash": lineage["lineage_hash"],
        "research_policy_id": policy.policy_id,
        "research_policy_hash": policy.policy_hash,
        "normalization": normalization,
        "score_method": "dimensionless_cohort_multi_objective_v1",
        "proxy_context_hash": proxy_context_hash,
    }
    return updated, rows, summary


def _audit_sampled_target_reads(loader, date_indices: list[int]) -> None:
    if getattr(loader, "physical_research_projection", False):
        return
    firewall = getattr(loader, "date_firewall", None)
    source_dates = list(getattr(loader, "firewall_source_trade_dates", None) or [])
    if firewall is None or not source_dates:
        return
    source_index = {date: index for index, date in enumerate(source_dates)}
    horizon = int(getattr(loader, "label_horizon", 1))
    for index in date_indices:
        start = loader.trade_dates[index]
        endpoint_index = source_index[start] + horizon
        if endpoint_index >= len(source_dates):
            raise RuntimeError(f"proxy target endpoint unavailable: {start}+{horizon}")
        firewall.assert_target_access(start, source_dates[endpoint_index], component="alpha_proxy_eval", purpose="sampled_target_read")


def _rank_ic(factor: torch.Tensor, target: torch.Tensor, validity: torch.Tensor) -> float:
    values = _daily_rank_ics(factor, target, validity, 2)
    return float(sum(values) / len(values)) if values else 0.0


def _daily_rank_ics(factor: torch.Tensor, target: torch.Tensor, validity: torch.Tensor, min_breadth: int) -> list[float]:
    values = []
    for idx in range(factor.shape[1]):
        mask = validity[:, idx]
        if int(mask.sum().item()) < int(min_breadth):
            continue
        x = _average_tie_rank(factor[mask, idx])
        y = _average_tie_rank(target[mask, idx])
        x = x - x.mean()
        y = y - y.mean()
        denom = torch.clamp(x.std(unbiased=False) * y.std(unbiased=False), min=1e-6)
        values.append(float((x * y).mean().item() / denom.item()))
    return values


def _turnover_proxy(factor: torch.Tensor, validity: torch.Tensor) -> float:
    if factor.shape[1] <= 1:
        return 0.0
    changes = []
    for date_index in range(1, factor.shape[1]):
        mask = validity[:, date_index - 1] & validity[:, date_index]
        if int(mask.sum().item()) < 2:
            continue
        previous = _average_tie_rank(factor[mask, date_index - 1])
        current = _average_tie_rank(factor[mask, date_index])
        changes.append(float((current - previous).abs().mean().item() / max(int(mask.sum().item()) - 1, 1)))
    return float(sum(changes) / len(changes)) if changes else 0.0


def _neutralize_proxy_factor(
    factor: torch.Tensor,
    validity: torch.Tensor,
    loader,
    date_tensor: torch.Tensor,
    eligible: torch.Tensor,
    policy: AlphaResearchPolicy,
) -> tuple[torch.Tensor, torch.Tensor, str]:
    raw = getattr(loader, "raw_data_cache", {})
    if not policy.proxy_neutralization.startswith("neutralize_"):
        transformed, transformed_validity = preprocess_factor_with_validity(
            factor,
            validity,
            raw,
            policy.proxy_neutralization,
            eligible,
        )
        return transformed, transformed_validity, "applied"
    required = ("industry_codes", "log_mkt_cap")
    missing = [name for name in required if name not in raw]
    if missing:
        if getattr(loader, "production_research", False):
            raise RuntimeError(f"PIT neutralization inputs missing: {','.join(missing)}")
        return (
            torch.where(validity & eligible, factor, torch.zeros_like(factor)),
            validity & eligible & torch.isfinite(factor),
            "nonproduction_fallback",
        )
    selected_raw = {}
    for name, value in raw.items():
        if not isinstance(value, torch.Tensor):
            continue
        aligned = value
        if aligned.ndim == 1:
            aligned = aligned.unsqueeze(1).expand(-1, len(loader.trade_dates))
        if aligned.ndim >= 2 and aligned.shape[1] == len(loader.trade_dates):
            aligned = aligned.index_select(1, date_tensor.to(device=aligned.device))
        selected_raw[name] = aligned.to(device=factor.device)
    governed_validity = validity & eligible
    validity_cache = getattr(loader, "raw_validity_cache", {}) or {}
    for name, aliases in {
        "log_mkt_cap": ("log_mkt_cap", "total_mv"),
        "industry_codes": ("industry_codes", "industry_code_matrix", "industry_status_known"),
    }.items():
        source_validity = None
        for alias in aliases:
            candidate = validity_cache.get(alias)
            if candidate is None:
                candidate = raw.get(f"{alias}_validity")
            if isinstance(candidate, torch.Tensor):
                source_validity = candidate
                break
        if source_validity is None:
            if getattr(loader, "production_research", False):
                raise RuntimeError(f"PIT neutralization validity missing: {name}")
            continue
        if source_validity.ndim == 1:
            source_validity = source_validity.unsqueeze(1).expand(-1, len(loader.trade_dates))
        source_validity = source_validity.index_select(1, date_tensor.to(device=source_validity.device))
        governed_validity &= source_validity.to(device=factor.device, dtype=torch.bool)
    transformed, transformed_validity = preprocess_factor_with_validity(
        factor,
        governed_validity,
        selected_raw,
        policy.proxy_neutralization,
        eligible,
    )
    return transformed, transformed_validity, "applied"


def _max_existing_correlation(
    factor: torch.Tensor,
    validity: torch.Tensor,
    existing: list[torch.Tensor],
    date_tensor: torch.Tensor,
) -> float:
    correlations = []
    for matrix in existing:
        reference = matrix.to(device=factor.device)
        if reference.ndim != 2 or reference.shape[0] != factor.shape[0]:
            raise RuntimeError("existing factor axis mismatch")
        if reference.shape[1] != factor.shape[1]:
            if int(date_tensor.max().item()) >= reference.shape[1]:
                raise RuntimeError("existing factor date axis mismatch")
            reference = reference.index_select(1, date_tensor.to(device=reference.device))
        mask = validity & torch.isfinite(reference)
        if int(mask.sum().item()) < 2:
            continue
        left = factor[mask].float()
        right = reference[mask].float()
        left = left - left.mean()
        right = right - right.mean()
        denom = left.norm() * right.norm()
        if float(denom.item()) > 1e-12:
            correlations.append(abs(float((left * right).sum().item() / denom.item())))
    return max(correlations, default=0.0)


def _universe_direction_metrics(
    factor: torch.Tensor,
    target: torch.Tensor,
    validity: torch.Tensor,
    loader,
    min_breadth: int,
) -> dict[str, object]:
    masks: dict[str, torch.Tensor] = {"all": torch.ones_like(validity, dtype=torch.bool)}
    raw = getattr(loader, "raw_data_cache", {})
    provided = raw.get("research_universe_masks")
    if isinstance(provided, dict):
        for name, value in provided.items():
            if isinstance(value, torch.Tensor) and value.shape == validity.shape:
                masks[str(name)] = value.to(device=validity.device, dtype=torch.bool)
    ts_codes = list(getattr(loader, "ts_codes", []) or [])
    if len(ts_codes) == factor.shape[0]:
        for name, suffix in (("sse", ".SH"), ("szse", ".SZ")):
            stock_mask = torch.tensor(
                [str(code).endswith(suffix) for code in ts_codes],
                dtype=torch.bool,
                device=validity.device,
            ).unsqueeze(1)
            masks.setdefault(name, stock_mask.expand_as(validity))
    rank_ic_by_universe: dict[str, float] = {}
    for name, mask in masks.items():
        values = _daily_rank_ics(factor, target, validity & mask, min_breadth)
        if values:
            rank_ic_by_universe[name] = float(sum(values) / len(values))
    anchor = rank_ic_by_universe.get("all")
    comparable = [value for name, value in rank_ic_by_universe.items() if name != "all"]
    if anchor is None:
        consistency = 0.0
    elif not comparable:
        consistency = 1.0
    elif abs(anchor) <= 1e-12:
        consistency = float(sum(abs(value) <= 1e-12 for value in comparable) / len(comparable))
    else:
        consistency = float(sum(value * anchor > 0 for value in comparable) / len(comparable))
    return {
        "rank_ic_by_universe": rank_ic_by_universe,
        "direction_consistency": consistency,
        "evaluable_universe_count": len(rank_ic_by_universe),
    }


def _average_tie_rank(values: torch.Tensor) -> torch.Tensor:
    order = torch.argsort(values, stable=True)
    sorted_values = values[order]
    sorted_ranks = torch.empty_like(sorted_values, dtype=torch.float32)
    start = 0
    while start < values.numel():
        end = start + 1
        while end < values.numel() and bool(sorted_values[end] == sorted_values[start]):
            end += 1
        sorted_ranks[start:end] = (start + end - 1) / 2.0
        start = end
    ranks = torch.empty_like(sorted_ranks)
    ranks[order] = sorted_ranks
    return ranks


def _loader_feature_validity(loader) -> torch.Tensor:
    validity = getattr(loader, "feature_validity", None)
    if validity is None:
        validity = getattr(loader, "feature_validity_tensor", None)
    if validity is None:
        if getattr(loader, "use_matrix_cache", False):
            raise RuntimeError("strict proxy requires feature validity tensor")
        validity = torch.isfinite(loader.feat_tensor)
    return validity.bool()


def _loader_target_available(loader) -> torch.Tensor:
    validity = getattr(loader, "target_available", None)
    if validity is None:
        validity = getattr(loader, "raw_data_cache", {}).get("target_available_mask")
    if validity is None:
        raise RuntimeError("strict target availability is required for proxy evaluation")
    if validity.shape != loader.target_ret.shape:
        raise RuntimeError("target availability shape mismatch")
    return validity.bool()


def _loader_signal_eligibility(loader) -> torch.Tensor:
    raw = getattr(loader, "raw_data_cache", {})
    for name in ("signal_eligible_at_close", "signal_eligible", "pit_available_mask"):
        if name in raw:
            return raw[name].bool()
    if getattr(loader, "production_research", False):
        raise RuntimeError("production proxy evaluation requires PIT signal eligibility")
    return torch.ones_like(loader.target_ret, dtype=torch.bool)


def _loader_research_indices(loader) -> tuple[list[int], str]:
    dates = tuple(str(value) for value in loader.trade_dates)
    if getattr(loader, "physical_research_projection", False):
        matrix_manifest = Path(getattr(loader, "matrix_cache_dir")) / "task_052a_strict_matrix_manifest.json"
        payload = json.loads(matrix_manifest.read_text(encoding="utf-8"))
        return list(range(len(dates))), str(payload["eligible_date_hash"])
    firewall = getattr(loader, "date_firewall", None)
    if firewall is None:
        payload = {"trade_dates": dates, "contract": "unbounded"}
        return list(range(len(dates))), hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    source_dates = tuple(getattr(loader, "firewall_source_trade_dates", ()) or dates)
    source_eligible = firewall.contract.eligible_dates(source_dates)
    if dates == source_eligible:
        return list(range(len(dates))), firewall.contract.eligible_date_hash(source_dates)
    return list(firewall.contract.eligible_indices(dates)), firewall.contract.eligible_date_hash(dates)

from dataclasses import replace


def score_candidates(candidates, proxy_rows, full_eval_rows, novelty_scores) -> tuple[list, list[dict]]:
    proxy_by_id = {row.get("alpha_candidate_id"): row for row in proxy_rows}
    full_by_hash = {}
    for row in full_eval_rows:
        request = row.get("request", {}) if isinstance(row.get("request"), dict) else {}
        formula_hash = request.get("formula_hash")
        if formula_hash:
            full_by_hash[formula_hash] = row
    updated = []
    scored_rows = []
    for candidate in candidates:
        proxy = proxy_by_id.get(candidate.alpha_candidate_id, {})
        full = full_by_hash.get(candidate.formula_hash, {})
        full_score = float(full.get("score", 0.0) or 0.0)
        proxy_score = float(proxy.get("proxy_score", candidate.proxy_score) or 0.0)
        novelty = float(novelty_scores.get(candidate.alpha_candidate_id, 0.5))
        final = float(0.8 * full_score + 0.2 * proxy_score) if full else float(proxy_score)
        status = candidate.status
        reject_reason = candidate.reject_reason
        full_status = str(full.get("status") or "")
        if status != "rejected" and proxy.get("status") == "proxy_passed":
            if full_status == "validation_candidate":
                status = "validation_candidate"
                reject_reason = None
            elif full_status == "data_blocked":
                status = "data_blocked"
                reject_reason = ";".join(full.get("gate_reasons") or full.get("data_blockers") or ["full_research_data_blocked"])
            elif full_status:
                status = "research_rejected"
                reject_reason = ";".join(full.get("gate_reasons") or ["full_eval_oos_gate_not_passed"])
            else:
                status = "research_evaluated"
                reject_reason = "positive_oos_evidence_missing"
        row = candidate.to_dict() | {
            "proxy_score": proxy_score,
            "full_eval_score": full_score,
            "novelty_score": novelty,
            "final_score": float(final),
            "score_components": {
                "full_eval_score": full_score,
                "proxy_score": proxy_score,
                "proxy_normalized_objectives": proxy.get("normalized_objectives") or {},
                "full_normalized_objectives": full.get("normalized_objectives") or {},
                "novelty_score": novelty,
                "aggregation": "0.8*full_standardized+0.2*proxy_standardized" if full else "proxy_standardized_only",
                "score_method": "dimensionless_cohort_multi_objective_v1",
            },
            "status": status,
            "reject_reason": reject_reason,
            "validation_status": full_status or "not_evaluated",
        }
        scored_rows.append(row)
        updated.append(
            replace(
                candidate,
                proxy_score=proxy_score,
                full_eval_score=full_score,
                novelty_score=novelty,
                final_score=float(final),
                status=status,
                validation_status=full_status or "not_evaluated",
                reject_reason=reject_reason,
                metadata={
                    **candidate.metadata,
                    "score_components": row["score_components"],
                    "gate_decision": full.get("gate_decision") if isinstance(full, dict) else None,
                },
            )
        )
    return updated, scored_rows

import hashlib
import json
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact


def write_trial_ledger(
    *,
    candidates,
    static_rows: list[dict[str, Any]],
    proxy_rows: list[dict[str, Any]],
    full_rows: list[dict[str, Any]],
    scored_rows: list[dict[str, Any]],
    shortlist,
    campaign_id: str,
    policy_id: str,
    policy_hash: str,
    output_dir: str | Path,
) -> tuple[dict[str, str], dict[str, Any]]:
    static_by_id = {str(row.get("alpha_candidate_id") or ""): row for row in static_rows}
    proxy_by_id = {str(row.get("alpha_candidate_id") or ""): row for row in proxy_rows}
    full_by_hash = {str(row.get("formula_hash") or (row.get("request") or {}).get("formula_hash") or ""): row for row in full_rows}
    scored_by_id = {str(row.get("alpha_candidate_id") or ""): row for row in scored_rows}
    shortlist_ids = {item.alpha_candidate_id for item in shortlist}
    rows = []
    for ordinal, candidate in enumerate(candidates):
        static = static_by_id.get(candidate.alpha_candidate_id, {})
        proxy = proxy_by_id.get(candidate.alpha_candidate_id, {})
        full = full_by_hash.get(candidate.formula_hash, {})
        scored = scored_by_id.get(candidate.alpha_candidate_id, {})
        rows.append(
            {
                "trial_ordinal": ordinal,
                "alpha_candidate_id": candidate.alpha_candidate_id,
                "formula_hash": candidate.formula_hash,
                "source": candidate.source,
                "family_tags": candidate.family_tags,
                "complexity": candidate.complexity,
                "lookback": candidate.lookback,
                "static_status": static.get("status", "not_evaluated"),
                "static_errors": static.get("errors") or [],
                "proxy_status": proxy.get("status", "not_evaluated"),
                "proxy_score": proxy.get("proxy_score"),
                "proxy_blockers": proxy.get("proxy_blockers") or [],
                "proxy_target_sampled": bool(proxy.get("sampled_dates")),
                "full_research_selected": bool(full),
                "full_research_status": full.get("status", "not_selected"),
                "full_research_score": full.get("score"),
                "raw_p_value": full.get("raw_p_value"),
                "bh_q_value": full.get("bh_q_value"),
                "selection_adjusted_p_value": full.get("selection_adjusted_p_value"),
                "final_status": scored.get("status", candidate.status),
                "final_score": scored.get("final_score", candidate.final_score),
                "shortlisted": candidate.alpha_candidate_id in shortlist_ids,
                "target_or_outcome_read": bool(proxy.get("sampled_dates")) or bool(full),
                "selection_data_reused": True,
                "untouched_holdout": False,
                "evidence_level": "retrospective_research_only",
            }
        )
    root_payload = {
        "campaign_id": campaign_id,
        "policy_hash": policy_hash,
        "rows": rows,
    }
    trial_root = hashlib.sha256(
        json.dumps(root_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    summary = _selection_bias_summary(rows) | {
        "campaign_id": campaign_id,
        "policy_id": policy_id,
        "policy_hash": policy_hash,
        "trial_root": trial_root,
        "status": "complete",
        "selection_data_reused": True,
        "untouched_holdout": False,
        "certification_ready": False,
    }
    target = Path(output_dir)
    ledger_path = write_jsonl_artifact(
        target / "alpha_trial_ledger.jsonl",
        rows,
        "alpha_trial_ledger",
        "alpha_factory",
    )
    report_path = write_json_artifact(
        target / "alpha_selection_bias_report.json",
        summary,
        "alpha_selection_bias_report",
        "alpha_factory",
    )
    return {
        "alpha_trial_ledger_path": str(ledger_path),
        "alpha_selection_bias_report_path": str(report_path),
    }, summary


def _selection_bias_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    counts = {
        "generated": total,
        "static_passed": sum(row["static_status"] == "passed" for row in rows),
        "proxy_passed": sum(row["proxy_status"] == "proxy_passed" for row in rows),
        "full_research_selected": sum(bool(row["full_research_selected"]) for row in rows),
        "validation_candidate": sum(row["full_research_status"] == "validation_candidate" for row in rows),
        "shortlisted": sum(bool(row["shortlisted"]) for row in rows),
    }
    stage_rates = {
        "static_pass_rate": counts["static_passed"] / max(counts["generated"], 1),
        "proxy_selection_rate": counts["proxy_passed"] / max(counts["static_passed"], 1),
        "full_selection_rate": counts["full_research_selected"] / max(counts["proxy_passed"], 1),
        "validation_candidate_rate": counts["validation_candidate"] / max(counts["full_research_selected"], 1),
        "shortlist_rate": counts["shortlisted"] / max(counts["generated"], 1),
    }
    source_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
        for family in row.get("family_tags") or ["general"]:
            family_counts[str(family)] = family_counts.get(str(family), 0) + 1
    return {
        "trial_count": total,
        "unique_formula_hash_count": len({row["formula_hash"] for row in rows}),
        "stage_counts": counts,
        "stage_rates": stage_rates,
        "source_trial_distribution": source_counts,
        "family_trial_distribution": family_counts,
        "target_exposed_trial_count": sum(bool(row["target_or_outcome_read"]) for row in rows),
        "minimum_bh_q_value": min((float(row["bh_q_value"]) for row in rows if row.get("bh_q_value") is not None), default=1.0),
    }

import hashlib
import json
import math
import random
from statistics import mean
from typing import Any

import torch

from auto_alpha.research.factors.engine import preprocess_factor_with_validity
from auto_alpha.research.factors.store import make_factor_id
from auto_alpha.research.formulas.vm import StackVM
from auto_alpha.validation.firewall.core_lineage import build_loader_lineage
from auto_alpha.validation.walk_forward.engine_metrics import evaluate_factor_dates
from auto_alpha.validation.walk_forward.engine_metrics import evaluate_factor_splits
from auto_alpha.validation.walk_forward.engine_policy import EngineeringRobustnessPolicy
from auto_alpha.validation.walk_forward.engine_splits import build_splits_for_eligible_segments

from auto_alpha.research.search.models import AlphaResearchPolicy


def run_full_research(
    candidates,
    loader,
    *,
    policy: AlphaResearchPolicy,
    vocab,
    factor_transform: str,
    total_trial_count: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    vm = StackVM(vocab)
    formula_root = hashlib.sha256(
        json.dumps(
            [
                {
                    "candidate_id": candidate.alpha_candidate_id,
                    "formula_hash": candidate.formula_hash,
                    "tokens": candidate.formula_tokens,
                    "lookback": candidate.lookback,
                }
                for candidate in candidates
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    lineage = build_loader_lineage(
        loader,
        stage="alpha_full_research",
        extra={
            "policy_hash": policy.policy_hash,
            "formula_root": formula_root,
            "total_trial_count": int(total_trial_count),
            "factor_transform": factor_transform,
            "seed": int(seed),
        },
    )
    target_available = _target_available(loader)
    signal_eligible = _signal_eligible(loader)
    validation_common = _validation_common(loader, signal_eligible, target_available)
    date_eligible = validation_common.sum(dim=0) >= int(policy.proxy_min_cross_section_breadth)
    segments = _eligible_segments(loader.trade_dates, date_eligible)
    beta = _asof_beta(loader, signal_eligible)
    rows: list[dict[str, Any]] = []
    for ordinal, candidate in enumerate(candidates):
        try:
            executed = vm.execute_with_validity(
                candidate.formula_tokens,
                loader.feat_tensor,
                _feature_validity(loader),
            )
            if executed is None:
                raise RuntimeError("StackVM returned no factor")
            raw_factor, formula_validity = executed
            formula_validity = _transform_input_validity(loader, formula_validity, factor_transform)
            factor, formula_validity = preprocess_factor_with_validity(
                raw_factor,
                formula_validity,
                loader.raw_data_cache,
                factor_transform,
                signal_eligible,
            )
            effective_embargo = int(candidate.lookback) + int(getattr(loader, "label_horizon", 0) or 0)
            splits = build_splits_for_eligible_segments(
                "rolling_walk_forward",
                segments,
                policy.train_size,
                policy.validation_size,
                policy.test_size,
                policy.step_size,
                effective_embargo,
                8,
                64,
            )
            validation_policy = _validation_policy(policy)
            windows, summary, issues = evaluate_factor_splits(
                factor,
                loader.target_ret,
                loader.trade_dates,
                splits,
                make_factor_id(candidate.formula_hash),
                validity=formula_validity,
                active_mask=_optional_mask(loader, "active_mask"),
                target_available_mask=target_available,
                index_member_mask=_optional_mask(loader, "index_member_matrix", "membership"),
                eligible_date_mask=date_eligible,
                validation_common_mask=validation_common,
                policy=validation_policy,
            )
            oos_dates = sorted({date for split in splits for date in split.test_dates})
            regime = _regime_diagnostics(
                factor,
                formula_validity,
                loader,
                oos_dates,
                validation_common,
                policy.proxy_min_cross_section_breadth,
            )
            placebo = _placebo_diagnostics(
                factor,
                formula_validity,
                loader,
                oos_dates,
                validation_common,
                policy,
                seed + ordinal,
                summary.out_of_sample_score,
            )
            time_sensitivity = _time_sensitivity(
                factor,
                formula_validity,
                loader,
                oos_dates,
                validation_common,
                policy.proxy_min_cross_section_breadth,
            )
            parameter_sensitivity = _parameter_sensitivity(
                factor,
                formula_validity,
                loader,
                oos_dates,
                validation_common,
                policy.proxy_min_cross_section_breadth,
            )
            stress = _cost_capacity_stress(
                factor,
                formula_validity,
                loader,
                oos_dates,
                validation_common,
                policy,
            )
            exposures = _style_exposures(
                factor,
                formula_validity,
                loader,
                beta,
                oos_dates,
                validation_common,
                policy.proxy_min_cross_section_breadth,
            )
            p_value = _aggregate_rank_ic_p_value(windows)
            pbo = _rolling_pbo(windows)
            data_blockers = [issue.code for issue in issues if issue.severity == "blocker" and issue.code in _DATA_BLOCKERS]
            if not stress["supported"]:
                data_blockers.append(str(stress["reason"]))
            if not exposures["supported"]:
                data_blockers.append(str(exposures["reason"]))
            statistical_blockers = [issue.code for issue in issues if issue.severity == "blocker" and issue.code not in _DATA_BLOCKERS]
            row = {
                "alpha_candidate_id": candidate.alpha_candidate_id,
                "factor_id": make_factor_id(candidate.formula_hash),
                "formula_hash": candidate.formula_hash,
                "request": {
                    "name": candidate.alpha_candidate_id,
                    "formula_hash": candidate.formula_hash,
                    "formula_tokens": candidate.formula_tokens,
                    "formula_names": candidate.formula_names,
                    "lookback": candidate.lookback,
                    "complexity": candidate.complexity,
                },
                "status": "full_research_evaluated",
                "score": None,
                "metrics_by_split": {
                    "all": summary.to_dict(),
                    "windows": [item.to_dict() for item in windows],
                },
                "validation_summary": summary.to_dict(),
                "validation_issues": [issue.to_dict() for issue in issues],
                "effective_embargo": effective_embargo,
                "split_count": len(splits),
                "oos_date_count": len(oos_dates),
                "oos_observation_count": int(
                    sum(float(item.test_metrics.get("n_observations") or 0.0) for item in windows)
                ),
                "mean_rank_ic": summary.mean_rank_ic,
                "mean_icir": summary.mean_icir,
                "window_pass_ratio": summary.window_pass_ratio,
                "stability_score": summary.stability_score,
                "train_test_decay": summary.train_test_decay,
                "placebo": placebo,
                "placebo_percentile": placebo["percentile"],
                "regime": regime,
                "regime_pass_ratio": regime["pass_ratio"],
                "time_sensitivity": time_sensitivity,
                "time_sensitivity_ratio": time_sensitivity["pass_ratio"],
                "parameter_sensitivity": parameter_sensitivity,
                "parameter_sensitivity_ratio": parameter_sensitivity["pass_ratio"],
                "cost_capacity_stress": stress,
                "modeled_net_spread": stress.get("modeled_net_spread"),
                "capacity_feasible_ratio": stress.get("capacity_feasible_ratio"),
                "style_exposures": exposures,
                "max_style_exposure": exposures.get("max_style_exposure"),
                "raw_p_value": p_value,
                "pbo_estimate": pbo,
                "pbo_method": "rolling_train_test_degradation_proxy_v1",
                "pbo_approximate": True,
                "data_blockers": sorted(set(data_blockers)),
                "statistical_blockers": sorted(set(statistical_blockers)),
                "research_policy_id": policy.policy_id,
                "research_policy_hash": policy.policy_hash,
                "lineage_hash": lineage["lineage_hash"],
                "score_method": "dimensionless_cohort_multi_objective_v1",
                "stress_evidence_level": "modeled_daily_bar_proxy",
                "certification_supported": False,
            }
            rows.append(row)
        except Exception as exc:
            rows.append(
                {
                    "alpha_candidate_id": candidate.alpha_candidate_id,
                    "factor_id": make_factor_id(candidate.formula_hash),
                    "formula_hash": candidate.formula_hash,
                    "request": {
                        "name": candidate.alpha_candidate_id,
                        "formula_hash": candidate.formula_hash,
                        "formula_tokens": candidate.formula_tokens,
                        "formula_names": candidate.formula_names,
                        "lookback": candidate.lookback,
                        "complexity": candidate.complexity,
                    },
                    "status": "data_blocked",
                    "score": 0.0,
                    "data_blockers": [f"full_research_failed:{type(exc).__name__}:{exc}"],
                    "statistical_blockers": [],
                    "research_policy_id": policy.policy_id,
                    "research_policy_hash": policy.policy_hash,
                    "lineage_hash": lineage["lineage_hash"],
                    "certification_supported": False,
                }
            )
    correction = _apply_multiple_testing(rows, max(int(total_trial_count), len(rows), 1))
    scoreable = [
        row
        for row in rows
        if row.get("status") == "full_research_evaluated"
        and not row.get("data_blockers")
        and all(_is_finite(row.get(spec.name)) for spec in policy.full_objectives if spec.required)
    ]
    scores, components, normalization = normalize_objective_rows(
        scoreable,
        policy.full_objectives,
        id_field="alpha_candidate_id",
    )
    for row in rows:
        if row.get("status") != "full_research_evaluated":
            continue
        candidate_id = str(row["alpha_candidate_id"])
        row["score"] = float(scores.get(candidate_id, 0.0))
        row["normalized_objectives"] = components.get(candidate_id, {})
        row["normalization_reference_hash"] = normalization["reference_hash"]
        _finalize_status(row, policy)
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    summary = {
        "enabled": True,
        "evaluated": len(rows),
        "status_counts": status_counts,
        "research_policy_id": policy.policy_id,
        "research_policy_hash": policy.policy_hash,
        "score_method": "dimensionless_cohort_multi_objective_v1",
        "normalization": normalization,
        "multiple_testing": correction,
        "pbo": {
            "method": "rolling_train_test_degradation_proxy_v1",
            "approximate": True,
            "certification_supported": False,
        },
        "selection_bias": {
            "total_trials": int(total_trial_count),
            "full_research_trials": len(rows),
            "selection_fraction": float(len(rows) / max(int(total_trial_count), 1)),
            "selection_data_reused": True,
            "untouched_holdout": False,
        },
        "certification_ready": False,
        "formula_root": formula_root,
        "lineage": lineage,
        "lineage_hash": lineage["lineage_hash"],
    }
    summary["content_hash"] = hashlib.sha256(
        json.dumps(
            {
                "rows": rows,
                "policy_hash": policy.policy_hash,
                "multiple_testing": correction,
                "normalization": normalization,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return rows, summary


def _validation_policy(policy: AlphaResearchPolicy) -> EngineeringRobustnessPolicy:
    return EngineeringRobustnessPolicy(
        policy_id=f"{policy.policy_id}:rolling_oos",
        train_size=policy.train_size,
        validation_size=policy.validation_size,
        test_size=policy.test_size,
        step_size=policy.step_size,
        min_cross_section_breadth=policy.proxy_min_cross_section_breadth,
        min_oos_dates=policy.test_size,
        min_coverage=policy.proxy_min_coverage,
        min_mean_rank_ic=policy.min_mean_rank_ic,
        min_mean_icir=policy.min_mean_icir,
        min_window_pass_ratio=policy.min_window_pass_ratio,
        max_train_test_decay=policy.max_train_test_decay,
        min_valid_oos_ratio=policy.min_valid_oos_dates / max(policy.test_size, 1),
        min_valid_oos_dates=policy.min_valid_oos_dates,
        min_evaluable_windows=policy.min_evaluable_windows,
        min_cumulative_oos_dates=policy.min_cumulative_oos_dates,
        parameters_locked=True,
    )


def _regime_diagnostics(factor, validity, loader, dates, common, min_breadth) -> dict[str, Any]:
    raw = loader.raw_data_cache
    close = raw.get("close")
    amount = raw.get("amount")
    if not isinstance(close, torch.Tensor) or not isinstance(amount, torch.Tensor):
        return {"pass_ratio": 0.0, "supported": False, "reason": "regime_inputs_missing", "regimes": {}}
    close_ret = torch.full_like(close, float("nan"))
    close_ret[:, 1:] = close[:, 1:] / close[:, :-1] - 1.0
    market = _masked_mean(close_ret, common, dim=0)
    trailing_vol = _rolling_std(market, 20)
    liquidity = _masked_mean(torch.log1p(torch.clamp(amount, min=0.0)), common, dim=0)
    index = {date: idx for idx, date in enumerate(loader.trade_dates)}
    date_indices = [index[date] for date in dates if date in index]
    vol_median = _finite_median(trailing_vol[date_indices])
    liq_median = _finite_median(liquidity[date_indices])
    buckets = {
        "market_up": [date for date in dates if math.isfinite(float(market[index[date]])) and float(market[index[date]]) > 0],
        "market_down": [date for date in dates if math.isfinite(float(market[index[date]])) and float(market[index[date]]) <= 0],
        "high_vol": [date for date in dates if math.isfinite(float(trailing_vol[index[date]])) and float(trailing_vol[index[date]]) > vol_median],
        "low_vol": [date for date in dates if math.isfinite(float(trailing_vol[index[date]])) and float(trailing_vol[index[date]]) <= vol_median],
        "high_liquidity": [date for date in dates if math.isfinite(float(liquidity[index[date]])) and float(liquidity[index[date]]) > liq_median],
        "low_liquidity": [date for date in dates if math.isfinite(float(liquidity[index[date]])) and float(liquidity[index[date]]) <= liq_median],
    }
    results = {}
    passed = 0
    for name, selected in buckets.items():
        metrics = _evaluate(factor, validity, loader, selected, common, min_breadth)
        ok = bool(metrics.get("evaluable")) and float(metrics.get("rank_ic_mean") or 0.0) >= 0.0
        passed += int(ok)
        results[name] = {"date_count": len(selected), "passed": ok, "metrics": metrics}
    return {"pass_ratio": passed / len(results) if results else 0.0, "supported": True, "regimes": results}


def _placebo_diagnostics(factor, validity, loader, dates, common, policy, seed, candidate_score) -> dict[str, Any]:
    rng = random.Random(seed)
    scores = []
    for trial in range(max(1, int(policy.placebo_trials))):
        generator = torch.Generator(device="cpu").manual_seed(rng.randrange(2**31))
        permuted = loader.target_ret.clone()
        for date in dates:
            idx = loader.trade_dates.index(date)
            mask = common[:, idx] & validity[:, idx] & torch.isfinite(permuted[:, idx])
            positions = torch.where(mask)[0]
            if positions.numel() < 2:
                continue
            order = torch.randperm(positions.numel(), generator=generator, device="cpu").to(positions.device)
            permuted[positions, idx] = permuted[positions[order], idx]
        metrics = evaluate_factor_dates(
            factor,
            permuted,
            loader.trade_dates,
            dates,
            validity=validity,
            target_available_mask=_target_available(loader),
            validation_common_mask=common,
            min_breadth=policy.proxy_min_cross_section_breadth,
        )
        scores.append(float(metrics.get("out_of_sample_score") or 0.0))
    percentile = sum(value <= float(candidate_score) for value in scores) / len(scores) if scores else 0.0
    return {
        "trial_count": len(scores),
        "percentile": float(percentile),
        "null_exceedance_ratio": float(sum(value >= float(candidate_score) for value in scores) / len(scores)) if scores else 1.0,
        "score_root": hashlib.sha256(json.dumps(scores, separators=(",", ":")).encode()).hexdigest(),
    }


def _time_sensitivity(factor, validity, loader, dates, common, min_breadth) -> dict[str, Any]:
    scenarios = {}
    baseline = _evaluate(factor, validity, loader, dates, common, min_breadth)
    delayed = torch.zeros_like(factor)
    delayed_validity = torch.zeros_like(validity)
    delayed[:, 1:] = factor[:, :-1]
    delayed_validity[:, 1:] = validity[:, :-1]
    scenarios["signal_lag_1"] = _evaluate(delayed, delayed_validity, loader, dates, common, min_breadth)
    midpoint = len(dates) // 2
    scenarios["early_oos"] = _evaluate(factor, validity, loader, dates[:midpoint], common, min_breadth)
    scenarios["late_oos"] = _evaluate(factor, validity, loader, dates[midpoint:], common, min_breadth)
    baseline_sign = math.copysign(1.0, float(baseline.get("rank_ic_mean") or 0.0))
    passed = [
        bool(value.get("evaluable"))
        and float(value.get("rank_ic_mean") or 0.0) * baseline_sign >= 0.0
        for value in scenarios.values()
    ]
    return {"pass_ratio": sum(passed) / len(passed), "baseline": baseline, "scenarios": scenarios}


def _parameter_sensitivity(factor, validity, loader, dates, common, min_breadth) -> dict[str, Any]:
    scenarios = {}
    for n_mad in (3.0, 5.0, 7.0):
        perturbed = torch.zeros_like(factor)
        for idx in range(factor.shape[1]):
            mask = validity[:, idx]
            values = factor[mask, idx]
            if values.numel() < 2:
                continue
            center = values.median()
            mad = (values - center).abs().median().clamp(min=1e-6)
            perturbed[mask, idx] = values.clamp(center - n_mad * mad, center + n_mad * mad)
        scenarios[f"winsor_mad_{int(n_mad)}"] = _evaluate(perturbed, validity, loader, dates, common, min_breadth)
    passed = [bool(value.get("evaluable")) and float(value.get("rank_ic_mean") or 0.0) >= 0.0 for value in scenarios.values()]
    return {"pass_ratio": sum(passed) / len(passed), "scenarios": scenarios}


def _cost_capacity_stress(factor, validity, loader, dates, common, policy) -> dict[str, Any]:
    amount = loader.raw_data_cache.get("amount")
    amount_semantics = getattr(loader, "amount_semantics", None) or loader.raw_data_cache.get("amount_semantics")
    if not isinstance(amount, torch.Tensor):
        return {"supported": False, "reason": "lagged_amount_missing"}
    if getattr(loader, "production_research", False) and amount_semantics != "raw_turnover_CNY":
        return {"supported": False, "reason": "amount_unit_contract_unproven"}
    index = {date: idx for idx, date in enumerate(loader.trade_dates)}
    gross = []
    selected_sets = []
    feasible = []
    per_name = float(policy.capacity_aum_cny) / 20.0
    for date in dates:
        idx = index.get(date)
        if idx is None:
            continue
        mask = common[:, idx] & validity[:, idx] & torch.isfinite(factor[:, idx]) & torch.isfinite(loader.target_ret[:, idx])
        positions = torch.where(mask)[0]
        if positions.numel() < policy.proxy_min_cross_section_breadth:
            continue
        top_n = min(20, int(positions.numel()))
        order = torch.argsort(factor[positions, idx], descending=True)[:top_n]
        selected = positions[order]
        selected_sets.append(set(int(value) for value in selected.tolist()))
        gross.append(float(loader.target_ret[selected, idx].mean().item()))
        capacity = amount[selected, idx]
        valid_capacity = torch.isfinite(capacity) & (capacity > 0)
        feasible.append(bool(valid_capacity.all() and torch.all(per_name <= capacity * float(policy.capacity_participation))))
    if not gross:
        return {"supported": False, "reason": "cost_capacity_no_evaluable_dates"}
    turnover = _set_turnover(selected_sets)
    gross_mean = float(mean(gross))
    modeled_cost = turnover * float(policy.modeled_cost_bps) / 10_000.0
    feasible_ratio = float(sum(feasible) / len(feasible)) if feasible else 0.0
    return {
        "supported": True,
        "evidence_level": "modeled_daily_bar_proxy",
        "lagged_amount_only": True,
        "amount_semantics": amount_semantics or "nonproduction_unproven",
        "gross_spread": gross_mean,
        "turnover": turnover,
        "modeled_cost": modeled_cost,
        "modeled_net_spread": gross_mean - modeled_cost,
        "double_modeled_cost_net_spread": gross_mean - 2.0 * modeled_cost,
        "capacity_feasible_ratio": feasible_ratio,
        "capacity_participation": float(policy.capacity_participation),
        "capacity_aum_cny": float(policy.capacity_aum_cny),
    }


def _style_exposures(factor, validity, loader, beta, dates, common, min_breadth) -> dict[str, Any]:
    raw = loader.raw_data_cache
    size = raw.get("log_mkt_cap")
    amount = raw.get("amount")
    industry = raw.get("industry_code_matrix", raw.get("industry_codes"))
    if not all(isinstance(value, torch.Tensor) for value in (size, amount, industry)) or beta is None:
        return {"supported": False, "reason": "style_exposure_inputs_missing"}
    size = _align(size, factor)
    amount = torch.log1p(torch.clamp(_align(amount, factor), min=0.0))
    industry = _align(industry, factor)
    index = {date: idx for idx, date in enumerate(loader.trade_dates)}
    size_corr = []
    beta_corr = []
    liquidity_corr = []
    concentrations = []
    for date in dates:
        idx = index.get(date)
        if idx is None:
            continue
        mask = common[:, idx] & validity[:, idx] & torch.isfinite(factor[:, idx])
        mask &= torch.isfinite(size[:, idx]) & torch.isfinite(amount[:, idx]) & torch.isfinite(beta[:, idx])
        if int(mask.sum().item()) < min_breadth:
            continue
        size_corr.append(_corr(factor[mask, idx], size[mask, idx]))
        beta_corr.append(_corr(factor[mask, idx], beta[mask, idx]))
        liquidity_corr.append(_corr(factor[mask, idx], amount[mask, idx]))
        positions = torch.where(mask)[0]
        top = positions[torch.argsort(factor[positions, idx], descending=True)[: min(20, int(positions.numel()))]]
        _, counts = torch.unique(industry[top, idx].long(), return_counts=True)
        concentrations.append(float(counts.max().item() / max(int(top.numel()), 1)))
    if not size_corr:
        return {"supported": False, "reason": "style_exposure_no_evaluable_dates"}
    summary = {
        "size_exposure": float(mean(size_corr)),
        "beta_exposure": float(mean(beta_corr)),
        "liquidity_exposure": float(mean(liquidity_corr)),
        "industry_concentration": float(mean(concentrations)),
    }
    summary["max_style_exposure"] = max(
        abs(summary["size_exposure"]),
        abs(summary["beta_exposure"]),
        abs(summary["liquidity_exposure"]),
        summary["industry_concentration"],
    )
    return {"supported": True, **summary}


def _apply_multiple_testing(rows: list[dict[str, Any]], total_trials: int) -> dict[str, Any]:
    valid = [(idx, float(row["raw_p_value"])) for idx, row in enumerate(rows) if row.get("raw_p_value") is not None]
    ordered = sorted(valid, key=lambda item: (item[1], str(rows[item[0]].get("formula_hash"))))
    m = max(len(ordered), 1)
    bh = [0.0] * len(ordered)
    running = 1.0
    for position in range(len(ordered) - 1, -1, -1):
        _, p_value = ordered[position]
        running = min(running, p_value * m / (position + 1))
        bh[position] = min(1.0, running)
    holm_running = 0.0
    for position, ((row_index, p_value), q_value) in enumerate(zip(ordered, bh)):
        holm_running = max(holm_running, min(1.0, (m - position) * p_value))
        rows[row_index]["bh_q_value"] = float(q_value)
        rows[row_index]["holm_adjusted_p_value"] = float(holm_running)
        rows[row_index]["selection_adjusted_p_value"] = float(min(1.0, p_value * total_trials))
    return {
        "method": "benjamini_hochberg_and_holm_v1",
        "total_generated_trials": int(total_trials),
        "full_research_trials": len(ordered),
        "effective_trial_count": len({row.get("formula_hash") for row in rows if row.get("formula_hash")}),
        "minimum_bh_q_value": min((value for value in bh), default=1.0),
    }


def _finalize_status(row: dict[str, Any], policy: AlphaResearchPolicy) -> None:
    if row.get("data_blockers"):
        row["status"] = "data_blocked"
        row["gate_reasons"] = list(row["data_blockers"])
        return
    blockers = list(row.get("statistical_blockers") or [])
    checks = (
        (float(row["mean_rank_ic"]) > 0.0, "positive_oos_rank_ic_missing"),
        (float(row["placebo_percentile"]) >= policy.min_placebo_percentile, "placebo_below_policy"),
        (float(row["regime_pass_ratio"]) >= policy.min_regime_pass_ratio, "regime_stability_below_policy"),
        (float(row["time_sensitivity_ratio"]) >= policy.min_time_sensitivity_ratio, "time_sensitivity_below_policy"),
        (float(row["parameter_sensitivity_ratio"]) >= policy.min_parameter_sensitivity_ratio, "parameter_sensitivity_below_policy"),
        (float(row["bh_q_value"]) <= policy.max_bh_q_value, "multiple_testing_q_value_above_policy"),
        (float(row["selection_adjusted_p_value"]) <= policy.max_selection_adjusted_p_value, "selection_adjusted_p_value_above_policy"),
        (float(row["pbo_estimate"]) <= policy.max_pbo, "pbo_above_policy"),
        (float(row["capacity_feasible_ratio"]) >= policy.min_capacity_feasible_ratio, "capacity_below_policy"),
        (float(row["cost_capacity_stress"]["double_modeled_cost_net_spread"]) >= 0.0, "double_cost_stress_failed"),
        (abs(float(row["style_exposures"]["size_exposure"])) <= policy.max_abs_size_exposure, "size_exposure_above_policy"),
        (abs(float(row["style_exposures"]["beta_exposure"])) <= policy.max_abs_beta_exposure, "beta_exposure_above_policy"),
        (abs(float(row["style_exposures"]["liquidity_exposure"])) <= policy.max_abs_liquidity_exposure, "liquidity_exposure_above_policy"),
        (float(row["style_exposures"]["industry_concentration"]) <= policy.max_industry_concentration, "industry_concentration_above_policy"),
    )
    blockers.extend(reason for passed, reason in checks if not passed)
    row["statistical_blockers"] = sorted(set(blockers))
    row["gate_reasons"] = row["statistical_blockers"]
    row["status"] = "validation_candidate" if not blockers else "research_rejected"
    row["gate_decision"] = {
        "passed": not blockers,
        "status": row["status"],
        "reasons": row["gate_reasons"],
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "positive_oos_evidence": not blockers,
        "checks": {
            "oos_evidence_positive": not blockers,
            "test_evaluable_date_count": int(row.get("oos_date_count") or 0),
            "test_valid_observation_count": int(row.get("oos_observation_count") or 0),
            "test_rank_ic_mean": float(row.get("mean_rank_ic") or 0.0),
            "test_rank_ic_ir": float(row.get("mean_icir") or 0.0),
            "window_pass_ratio": float(row.get("window_pass_ratio") or 0.0),
            "bh_q_value": float(row.get("bh_q_value") or 1.0),
            "selection_adjusted_p_value": float(row.get("selection_adjusted_p_value") or 1.0),
        },
        "certification_supported": False,
    }


def _evaluate(factor, validity, loader, dates, common, min_breadth):
    return evaluate_factor_dates(
        factor,
        loader.target_ret,
        loader.trade_dates,
        dates,
        validity=validity,
        active_mask=_optional_mask(loader, "active_mask"),
        target_available_mask=_target_available(loader),
        index_member_mask=_optional_mask(loader, "index_member_matrix", "membership"),
        validation_common_mask=common,
        min_breadth=min_breadth,
    )


def _aggregate_rank_ic_p_value(windows) -> float:
    values = [float(item.test_metrics.get("rank_ic_t_stat") or 0.0) for item in windows if item.test_metrics.get("evaluable")]
    if not values:
        return 1.0
    z_value = sum(values) / math.sqrt(len(values))
    return float(math.erfc(abs(z_value) / math.sqrt(2.0)))


def _rolling_pbo(windows) -> float:
    comparable = []
    for item in windows:
        train = item.train_metrics.get("out_of_sample_score")
        test = item.test_metrics.get("out_of_sample_score")
        if train is not None and test is not None:
            comparable.append(float(test) < float(train))
    return float(sum(comparable) / len(comparable)) if comparable else 1.0


def _asof_beta(loader, signal_eligible) -> torch.Tensor | None:
    close = loader.raw_data_cache.get("close")
    if not isinstance(close, torch.Tensor):
        return None
    returns = torch.full_like(close, float("nan"))
    valid = torch.isfinite(close) & (close > 0)
    pair = valid[:, 1:] & valid[:, :-1]
    returns[:, 1:] = torch.where(pair, close[:, 1:] / close[:, :-1] - 1.0, torch.full_like(close[:, 1:], float("nan")))
    market = _masked_mean(returns, signal_eligible & torch.isfinite(returns), dim=0)
    beta = torch.full_like(close, float("nan"))
    for idx in range(close.shape[1]):
        start = max(1, idx - 59)
        stock = returns[:, start : idx + 1]
        benchmark = market[start : idx + 1].unsqueeze(0).expand_as(stock)
        mask = torch.isfinite(stock) & torch.isfinite(benchmark)
        count = mask.sum(dim=1)
        stock_mean = torch.where(mask, stock, torch.zeros_like(stock)).sum(dim=1) / count.clamp(min=1)
        market_mean = torch.where(mask, benchmark, torch.zeros_like(benchmark)).sum(dim=1) / count.clamp(min=1)
        covariance = torch.where(mask, (stock - stock_mean[:, None]) * (benchmark - market_mean[:, None]), torch.zeros_like(stock)).sum(dim=1)
        variance = torch.where(mask, (benchmark - market_mean[:, None]) ** 2, torch.zeros_like(benchmark)).sum(dim=1)
        valid_beta = (count >= 20) & (variance > 1e-12)
        beta[valid_beta, idx] = covariance[valid_beta] / variance[valid_beta]
    return beta


def _feature_validity(loader):
    value = getattr(loader, "feature_validity", None)
    if value is None:
        value = getattr(loader, "feature_validity_tensor", None)
    if value is None:
        raise RuntimeError("feature validity tensor missing")
    return value.bool()


def _transform_input_validity(loader, formula_validity, method):
    if not str(method).startswith("neutralize_"):
        return formula_validity
    validity_cache = getattr(loader, "raw_validity_cache", {}) or {}
    required = ["total_mv"] if method == "neutralize_market_cap" else ["industry_codes"]
    if method == "neutralize_industry_size":
        required = ["total_mv", "industry_codes"]
    result = formula_validity.bool()
    for name in required:
        aliases = ("log_mkt_cap", "total_mv") if name == "total_mv" else ("industry_codes", "industry_code_matrix", "industry_status_known")
        mask = next((validity_cache.get(alias) for alias in aliases if isinstance(validity_cache.get(alias), torch.Tensor)), None)
        if mask is None:
            if getattr(loader, "production_research", False):
                raise RuntimeError(f"transform validity missing: {name}")
            continue
        if mask.ndim == 1:
            mask = mask.unsqueeze(1).expand_as(result)
        result &= mask.to(device=result.device, dtype=torch.bool)
    return result


def _target_available(loader):
    value = getattr(loader, "target_available", None)
    if value is None:
        value = loader.raw_data_cache.get("target_available_mask")
    if not isinstance(value, torch.Tensor) or value.shape != loader.target_ret.shape:
        raise RuntimeError("strict target availability missing")
    return value.bool()


def _signal_eligible(loader):
    for name in ("signal_candidate_cells", "signal_eligible_at_close", "signal_eligible", "pit_available_mask"):
        value = loader.raw_data_cache.get(name)
        if isinstance(value, torch.Tensor) and value.shape == loader.target_ret.shape:
            return value.bool()
    raise RuntimeError("PIT signal eligibility missing")


def _validation_common(loader, signal_eligible, target_available):
    value = loader.raw_data_cache.get("validation_common_cells")
    if isinstance(value, torch.Tensor) and value.shape == loader.target_ret.shape:
        return value.bool()
    if getattr(loader, "production_research", False):
        raise RuntimeError("validation_common_cells missing")
    return signal_eligible & target_available & torch.isfinite(loader.target_ret)


def _optional_mask(loader, *names):
    for name in names:
        value = loader.raw_data_cache.get(name)
        if isinstance(value, torch.Tensor) and value.shape == loader.target_ret.shape:
            return value.bool()
    return None


def _eligible_segments(dates, mask):
    segments = []
    start = None
    values = mask.detach().bool().cpu().tolist()
    for idx, value in enumerate(values + [False]):
        if value and start is None:
            start = idx
        elif not value and start is not None:
            segments.append(list(dates[start:idx]))
            start = None
    return segments


def _align(value, reference):
    if value.ndim == 1:
        return value.to(reference.device).unsqueeze(1).expand_as(reference)
    return value.to(reference.device)


def _corr(left, right):
    mask = torch.isfinite(left) & torch.isfinite(right)
    if int(mask.sum().item()) < 2:
        return 0.0
    x = left[mask].float()
    y = right[mask].float()
    x = x - x.mean()
    y = y - y.mean()
    denom = x.norm() * y.norm()
    return float((x * y).sum().item() / denom.item()) if float(denom.item()) > 1e-12 else 0.0


def _masked_mean(values, mask, dim):
    valid = mask & torch.isfinite(values)
    return torch.where(valid, values, torch.zeros_like(values)).sum(dim=dim) / valid.sum(dim=dim).clamp(min=1)


def _rolling_std(values, window):
    result = torch.full_like(values, float("nan"))
    for idx in range(values.numel()):
        selected = values[max(0, idx - window + 1) : idx + 1]
        selected = selected[torch.isfinite(selected)]
        if selected.numel() >= max(2, window // 2):
            result[idx] = selected.std(unbiased=False)
    return result


def _finite_median(values):
    finite = values[torch.isfinite(values)]
    return float(finite.median().item()) if finite.numel() else 0.0


def _is_finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _set_turnover(sets):
    if len(sets) < 2:
        return 0.0
    values = []
    for left, right in zip(sets[:-1], sets[1:]):
        values.append(1.0 - len(left & right) / max(len(left | right), 1))
    return float(mean(values))


_DATA_BLOCKERS = {
    "data_blocked_window",
    "no_oos_windows",
    "insufficient_evaluable_windows",
    "insufficient_cumulative_oos_dates",
    "insufficient_oos_dates",
    "no_valid_factor_values",
    "zero_variance_factor",
    "all_zero_factor",
}
