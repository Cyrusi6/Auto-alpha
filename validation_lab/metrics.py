"""Validation metrics built on aligned factor values and targets."""

from __future__ import annotations

import math
from statistics import mean, pstdev

import torch

from .models import FactorValidationSummary, FactorValidationWindowResult, ValidationIssue, ValidationSplit
from .policy import EngineeringRobustnessPolicy, load_validation_policy


def evaluate_factor_splits(
    factors: torch.Tensor,
    target_ret: torch.Tensor,
    trade_dates: list[str],
    splits: list[ValidationSplit],
    factor_id: str,
    *,
    validity: torch.Tensor | None = None,
    active_mask: torch.Tensor | None = None,
    target_available_mask: torch.Tensor | None = None,
    index_member_mask: torch.Tensor | None = None,
    eligible_date_mask: torch.Tensor | None = None,
    validation_common_mask: torch.Tensor | None = None,
    policy: EngineeringRobustnessPolicy | None = None,
) -> tuple[list[FactorValidationWindowResult], FactorValidationSummary, list[ValidationIssue]]:
    policy = policy or EngineeringRobustnessPolicy(
        policy_id="legacy_api_compatibility_only",
        min_cross_section_breadth=2,
        min_oos_dates=0,
        min_standard_deviation=-1.0,
        min_mean_rank_ic=-1.0,
        min_mean_icir=-1e9,
        min_window_pass_ratio=0.0,
        max_train_test_decay=1e9,
        min_valid_oos_ratio=0.0,
        min_valid_oos_dates=0,
        min_evaluable_windows=0,
        min_cumulative_oos_dates=0,
    )
    date_index = {date: idx for idx, date in enumerate(trade_dates)}
    results: list[FactorValidationWindowResult] = []
    issues: list[ValidationIssue] = []
    daily_cache: dict[int, dict | None] = {}
    for split in splits:
        split_test_dates = [date for date in split.test_dates if eligible_date_mask is None or bool(eligible_date_mask[date_index[date]].item())]
        metric_args = (
            factors, target_ret, date_index, validity, active_mask, target_available_mask,
            index_member_mask, validation_common_mask, policy.min_cross_section_breadth, daily_cache,
        )
        train = _metrics_for_dates(*metric_args[:2], split.train_dates, *metric_args[2:])
        valid = _metrics_for_dates(*metric_args[:2], split.validation_dates, *metric_args[2:])
        test = _metrics_for_dates(*metric_args[:2], split_test_dates, *metric_args[2:])
        warnings = []
        if not test.get("evaluable", False):
            issues.append(ValidationIssue("blocker", "data_blocked_window", f"split {split.split_id} has no evaluable OOS data", {"split_id": split.split_id}))
            continue
        if test.get("n_observations", 0.0) < 2:
            warnings.append("insufficient_test_observations")
            issues.append(ValidationIssue("warning", "insufficient_test_observations", f"split {split.split_id} has too few test observations", {"split_id": split.split_id}))
        valid_ratio = float(test.get("n_dates", 0.0) / max(len(split.test_dates), 1))
        if test.get("n_dates", 0.0) < policy.min_valid_oos_dates or valid_ratio < policy.min_valid_oos_ratio:
            warnings.append("insufficient_oos_dates")
            issues.append(
                ValidationIssue(
                    severity="blocker",
                    code="insufficient_oos_dates",
                    message=f"split {split.split_id} does not meet valid OOS date policy",
                    metadata={"split_id": split.split_id, "n_dates": test.get("n_dates", 0.0), "valid_ratio": valid_ratio},
                )
            )
        results.append(
            FactorValidationWindowResult(
                split_id=split.split_id,
                method=split.method,
                train_metrics=train,
                validation_metrics=valid,
                test_metrics=test,
                warnings=warnings,
            )
        )
    factor_quality_mask = torch.isfinite(factors)
    if validity is not None and validity.shape == factors.shape:
        factor_quality_mask &= validity.bool()
    if validation_common_mask is not None and validation_common_mask.shape == factors.shape:
        factor_quality_mask &= validation_common_mask.bool()
    if eligible_date_mask is not None and eligible_date_mask.numel() == factors.shape[1]:
        factor_quality_mask &= eligible_date_mask.bool().reshape(1, -1).expand_as(factors)
    finite_valid = factors[factor_quality_mask]
    if finite_valid.numel() == 0:
        issues.append(ValidationIssue("blocker", "no_valid_factor_values", "factor has no valid values"))
    else:
        standard_deviation = float(finite_valid.float().std(unbiased=False).item())
        nonzero_ratio = float((finite_valid != 0).float().mean().item())
        if standard_deviation <= policy.min_standard_deviation:
            issues.append(ValidationIssue("blocker", "zero_variance_factor", "factor is constant or zero variance", {"standard_deviation": standard_deviation}))
        if nonzero_ratio <= 1e-8:
            issues.append(ValidationIssue("blocker", "all_zero_factor", "factor contains only zeros"))
    cumulative_oos = sum(int(item.test_metrics.get("n_dates", 0)) for item in results)
    if len(results) < policy.min_evaluable_windows:
        issues.append(ValidationIssue("blocker", "insufficient_evaluable_windows", "too few evaluable OOS windows", {"count": len(results), "minimum": policy.min_evaluable_windows}))
    if cumulative_oos < policy.min_cumulative_oos_dates:
        issues.append(ValidationIssue("blocker", "insufficient_cumulative_oos_dates", "too few cumulative OOS dates", {"count": cumulative_oos, "minimum": policy.min_cumulative_oos_dates}))
    summary = summarize_window_results(factor_id, splits[0].method if splits else "unknown", results, issues, policy=policy)
    return results, summary, issues


def summarize_window_results(
    factor_id: str,
    split_method: str,
    results: list[FactorValidationWindowResult],
    issues: list[ValidationIssue] | None = None,
    policy: EngineeringRobustnessPolicy | None = None,
) -> FactorValidationSummary:
    policy = policy or EngineeringRobustnessPolicy(
        policy_id="legacy_api_compatibility_only",
        min_cross_section_breadth=2,
        min_oos_dates=0,
        min_standard_deviation=-1.0,
        min_mean_rank_ic=-1.0,
        min_mean_icir=-1e9,
        min_window_pass_ratio=0.0,
        max_train_test_decay=1e9,
        min_valid_oos_ratio=0.0,
        min_valid_oos_dates=0,
        min_evaluable_windows=0,
        min_cumulative_oos_dates=0,
    )
    test_scores = [_finite(item.test_metrics["out_of_sample_score"]) for item in results if item.test_metrics.get("evaluable")]
    rank_ics = [_finite(item.test_metrics["rank_ic_mean"]) for item in results if item.test_metrics.get("evaluable")]
    icirs = [_finite(item.test_metrics["icir"]) for item in results if item.test_metrics.get("evaluable")]
    train_scores = [_finite(item.train_metrics["out_of_sample_score"]) for item in results if item.train_metrics.get("evaluable")]
    if not test_scores:
        issues = list(issues or []) + [ValidationIssue("blocker", "no_oos_windows", "no out-of-sample windows were evaluated")]
    avg_score = float(mean(test_scores)) if test_scores else 0.0
    score_std = float(pstdev(test_scores)) if len(test_scores) > 1 else 0.0
    pass_ratio = float(sum(score >= 0.0 for score in test_scores) / len(test_scores)) if test_scores else 0.0
    train_mean = float(mean(train_scores)) if train_scores else 0.0
    train_test_decay = float(train_mean - avg_score)
    threshold_failures = []
    if (float(mean(rank_ics)) if rank_ics else 0.0) < policy.min_mean_rank_ic:
        threshold_failures.append(("mean_rank_ic_below_policy", float(mean(rank_ics)) if rank_ics else 0.0, policy.min_mean_rank_ic))
    if (float(mean(icirs)) if icirs else 0.0) < policy.min_mean_icir:
        threshold_failures.append(("mean_icir_below_policy", float(mean(icirs)) if icirs else 0.0, policy.min_mean_icir))
    if pass_ratio < policy.min_window_pass_ratio:
        threshold_failures.append(("window_pass_ratio_below_policy", pass_ratio, policy.min_window_pass_ratio))
    if train_test_decay > policy.max_train_test_decay:
        threshold_failures.append(("train_test_decay_above_policy", train_test_decay, policy.max_train_test_decay))
    issue_rows = list(issues or [])
    issue_rows.extend(ValidationIssue("blocker", code, f"policy threshold failed: {value} vs {threshold}", {"value": value, "threshold": threshold, "policy_id": policy.policy_id}) for code, value, threshold in threshold_failures)
    blocker_count = sum(1 for issue in issue_rows if issue.severity == "blocker")
    warning_count = sum(1 for issue in issue_rows if issue.severity == "warning")
    data_blocker_codes = {
        "data_blocked_window",
        "no_oos_windows",
        "insufficient_evaluable_windows",
        "insufficient_cumulative_oos_dates",
        "insufficient_oos_dates",
        "no_valid_factor_values",
    }
    if any(issue.severity == "blocker" and issue.code in data_blocker_codes for issue in issue_rows):
        status = "data_blocked"
    elif blocker_count:
        status = "statistically_rejected"
    else:
        status = "engineering_passed"
    return FactorValidationSummary(
        factor_id=factor_id,
        split_method=split_method,
        split_count=len(results),
        out_of_sample_score=avg_score,
        cost_adjusted_score=None,
        capacity_adjusted_score=None,
        risk_adjusted_score=avg_score / (1.0 + score_std),
        window_pass_ratio=pass_ratio,
        stability_score=float(1.0 / (1.0 + score_std)),
        mean_rank_ic=float(mean(rank_ics)) if rank_ics else 0.0,
        mean_icir=float(mean(icirs)) if icirs else 0.0,
        train_test_decay=train_test_decay,
        max_single_window_loss=float(min(test_scores)) if test_scores else 0.0,
        blocker_count=blocker_count,
        warning_count=warning_count,
        status=status,
        metrics={
            "score_std": score_std,
            "window_count": float(len(results)),
            "positive_score_windows": float(sum(score >= 0.0 for score in test_scores)),
            "policy_version": 2.0,
        },
        unsupported_metrics=["cost_adjusted_score", "capacity_adjusted_score", "drawdown"],
    )


def _metrics_for_dates(
    factors: torch.Tensor,
    target_ret: torch.Tensor,
    dates: list[str],
    date_index: dict[str, int],
    validity: torch.Tensor | None = None,
    active_mask: torch.Tensor | None = None,
    target_available_mask: torch.Tensor | None = None,
    index_member_mask: torch.Tensor | None = None,
    validation_common_mask: torch.Tensor | None = None,
    min_breadth: int = 2,
    daily_cache: dict[int, dict | None] | None = None,
) -> dict[str, float]:
    if not dates:
        return _empty_metrics()
    rank_ics = []
    spreads = []
    coverages = []
    turnovers = []
    prev_selected: set[int] | None = None
    obs = 0
    losses = []
    for trade_date in dates:
        idx = date_index.get(trade_date)
        if idx is None or idx >= factors.shape[1] or idx >= target_ret.shape[1]:
            continue
        cached = daily_cache.get(idx) if daily_cache is not None and idx in daily_cache else None
        if daily_cache is not None and idx in daily_cache and cached is None:
            continue
        if cached is None:
            x = factors[:, idx].detach().float().cpu()
            y = target_ret[:, idx].detach().float().cpu()
            mask = torch.isfinite(x) & torch.isfinite(y)
            for governed_mask in (validity, active_mask, target_available_mask, index_member_mask, validation_common_mask):
                if governed_mask is not None:
                    mask &= governed_mask[:, idx].detach().bool().cpu()
            breadth = int(mask.sum().item())
            if breadth < int(min_breadth):
                if daily_cache is not None:
                    daily_cache[idx] = None
                continue
            xv = x[mask]
            yv = y[mask]
            spread, selected = _top_bottom_spread(xv, yv, mask.nonzero().flatten().tolist())
            denominator_mask = (
                validation_common_mask[:, idx].detach().bool().cpu()
                if validation_common_mask is not None
                else torch.ones_like(mask, dtype=torch.bool)
            )
            denominator = int(denominator_mask.sum().item())
            cached = {
                "observations": breadth,
                "coverage": float(breadth / denominator) if denominator else 0.0,
                "rank_ic": _pearson(_rank(xv), _rank(yv)),
                "spread": spread,
                "selected": selected,
            }
            if daily_cache is not None:
                daily_cache[idx] = cached
        obs += int(cached["observations"])
        coverages.append(float(cached["coverage"]))
        rank_ics.append(float(cached["rank_ic"]))
        spread = float(cached["spread"])
        selected = set(cached["selected"])
        spreads.append(spread)
        losses.append(spread)
        if prev_selected is not None:
            union = selected | prev_selected
            if union:
                turnovers.append(1.0 - len(selected & prev_selected) / len(union))
        prev_selected = selected
    if not rank_ics:
        return _empty_metrics()
    rank_mean = float(mean(rank_ics))
    rank_std = float(pstdev(rank_ics)) if len(rank_ics) > 1 else 0.0
    icir = rank_mean / rank_std if rank_std > 1e-12 else rank_mean
    spread_mean = float(mean(spreads)) if spreads else 0.0
    turnover = float(mean(turnovers)) if turnovers else 0.0
    monotonicity = 1.0 if spread_mean >= 0 else -1.0
    score = rank_mean + spread_mean + 0.1 * monotonicity - 0.1 * turnover
    t_stat = rank_mean / (rank_std / math.sqrt(len(rank_ics))) if rank_std > 1e-12 and len(rank_ics) > 1 else rank_mean
    return {
        "evaluable": True,
        "rank_ic_mean": _finite(rank_mean),
        "rank_ic_std": _finite(rank_std),
        "rank_ic_t_stat": _finite(t_stat),
        "rank_ic_hit_rate": _finite(sum(value > 0 for value in rank_ics) / len(rank_ics)),
        "icir": _finite(icir),
        "quantile_spread": _finite(spread_mean),
        "quantile_monotonicity_score": _finite(monotonicity),
        "coverage_mean": _finite(float(mean(coverages)) if coverages else 0.0),
        "turnover_mean": _finite(turnover),
        "train_valid_decay": 0.0,
        "train_test_decay": 0.0,
        "out_of_sample_score": _finite(score),
        "cost_adjusted_score": None,
        "capacity_adjusted_score": None,
        "risk_adjusted_score": _finite(score / (1.0 + abs(rank_std))),
        "drawdown": None,
        "max_single_window_loss": _finite(min(losses) if losses else 0.0),
        "window_pass_ratio": _finite(sum(value >= 0 for value in spreads) / len(spreads) if spreads else 0.0),
        "stability_score": _finite(1.0 / (1.0 + rank_std)),
        "n_dates": float(len(rank_ics)),
        "n_observations": float(obs),
    }


def _empty_metrics() -> dict[str, float | bool | None]:
    keys = [
        "rank_ic_mean",
        "rank_ic_std",
        "rank_ic_t_stat",
        "rank_ic_hit_rate",
        "icir",
        "quantile_spread",
        "quantile_monotonicity_score",
        "coverage_mean",
        "turnover_mean",
        "train_valid_decay",
        "train_test_decay",
        "out_of_sample_score",
        "cost_adjusted_score",
        "capacity_adjusted_score",
        "risk_adjusted_score",
        "drawdown",
        "max_single_window_loss",
        "window_pass_ratio",
        "stability_score",
        "n_dates",
        "n_observations",
    ]
    return {**{key: None for key in keys}, "n_dates": 0.0, "n_observations": 0.0, "evaluable": False}


def _rank(values: torch.Tensor) -> torch.Tensor:
    order = torch.argsort(values, stable=True)
    sorted_values = values[order]
    ranks = torch.empty_like(values, dtype=torch.float32)
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and bool(sorted_values[end] == sorted_values[start]):
            end += 1
        average_rank = (start + end - 1) / 2.0
        ranks[order[start:end]] = average_rank
        start = end
    return ranks


def _pearson(x: torch.Tensor, y: torch.Tensor) -> float:
    x = x.float()
    y = y.float()
    x = x - x.mean()
    y = y - y.mean()
    denom = torch.sqrt((x * x).sum() * (y * y).sum())
    if float(denom.item()) <= 1e-12:
        return 0.0
    return _finite(float(((x * y).sum() / denom).item()))


def _top_bottom_spread(x: torch.Tensor, y: torch.Tensor, original_indices: list[int]) -> tuple[float, set[int]]:
    n = int(x.numel())
    if n < 2:
        return 0.0, set()
    top_n = max(1, n // 3)
    order = torch.argsort(x, descending=True)
    top = order[:top_n]
    bottom = order[-top_n:]
    spread = float(y[top].mean().item() - y[bottom].mean().item())
    selected = {original_indices[int(idx)] for idx in top.tolist()}
    return _finite(spread), selected


def _finite(value: float) -> float:
    value = float(value)
    return value if math.isfinite(value) else 0.0
