"""Validation metrics built on aligned factor values and targets."""

from __future__ import annotations

import math
from statistics import mean, pstdev

import torch

from .models import FactorValidationSummary, FactorValidationWindowResult, ValidationIssue, ValidationSplit


def evaluate_factor_splits(
    factors: torch.Tensor,
    target_ret: torch.Tensor,
    trade_dates: list[str],
    splits: list[ValidationSplit],
    factor_id: str,
) -> tuple[list[FactorValidationWindowResult], FactorValidationSummary, list[ValidationIssue]]:
    date_index = {date: idx for idx, date in enumerate(trade_dates)}
    results: list[FactorValidationWindowResult] = []
    issues: list[ValidationIssue] = []
    for split in splits:
        train = _metrics_for_dates(factors, target_ret, split.train_dates, date_index)
        valid = _metrics_for_dates(factors, target_ret, split.validation_dates, date_index)
        test = _metrics_for_dates(factors, target_ret, split.test_dates, date_index)
        warnings = []
        if test.get("n_observations", 0.0) < 2:
            warnings.append("insufficient_test_observations")
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="insufficient_test_observations",
                    message=f"split {split.split_id} has too few test observations",
                    metadata={"split_id": split.split_id},
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
    summary = summarize_window_results(factor_id, splits[0].method if splits else "unknown", results, issues)
    return results, summary, issues


def summarize_window_results(
    factor_id: str,
    split_method: str,
    results: list[FactorValidationWindowResult],
    issues: list[ValidationIssue] | None = None,
) -> FactorValidationSummary:
    test_scores = [_finite(item.test_metrics.get("out_of_sample_score", 0.0)) for item in results]
    rank_ics = [_finite(item.test_metrics.get("rank_ic_mean", 0.0)) for item in results]
    icirs = [_finite(item.test_metrics.get("icir", 0.0)) for item in results]
    train_scores = [_finite(item.train_metrics.get("out_of_sample_score", 0.0)) for item in results]
    if not test_scores:
        test_scores = [0.0]
    avg_score = float(mean(test_scores))
    score_std = float(pstdev(test_scores)) if len(test_scores) > 1 else 0.0
    pass_ratio = float(sum(score >= 0.0 for score in test_scores) / len(test_scores))
    train_mean = float(mean(train_scores)) if train_scores else 0.0
    train_test_decay = float(train_mean - avg_score)
    blocker_count = sum(1 for issue in issues or [] if issue.severity == "blocker")
    warning_count = sum(1 for issue in issues or [] if issue.severity == "warning")
    status = "passed" if blocker_count == 0 else "blocked"
    return FactorValidationSummary(
        factor_id=factor_id,
        split_method=split_method,
        split_count=len(results),
        out_of_sample_score=avg_score,
        cost_adjusted_score=avg_score - 0.01,
        capacity_adjusted_score=avg_score - 0.01,
        risk_adjusted_score=avg_score / (1.0 + score_std),
        window_pass_ratio=pass_ratio,
        stability_score=float(1.0 / (1.0 + score_std)),
        mean_rank_ic=float(mean(rank_ics)) if rank_ics else 0.0,
        mean_icir=float(mean(icirs)) if icirs else 0.0,
        train_test_decay=train_test_decay,
        max_single_window_loss=float(min(test_scores)),
        blocker_count=blocker_count,
        warning_count=warning_count,
        status=status,
        metrics={
            "score_std": score_std,
            "window_count": float(len(results)),
            "positive_score_windows": float(sum(score >= 0.0 for score in test_scores)),
        },
    )


def _metrics_for_dates(
    factors: torch.Tensor,
    target_ret: torch.Tensor,
    dates: list[str],
    date_index: dict[str, int],
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
        x = factors[:, idx].detach().float().cpu()
        y = target_ret[:, idx].detach().float().cpu()
        mask = torch.isfinite(x) & torch.isfinite(y)
        if int(mask.sum().item()) < 2:
            continue
        xv = x[mask]
        yv = y[mask]
        obs += int(mask.sum().item())
        coverages.append(float(mask.float().mean().item()))
        rank_ics.append(_pearson(_rank(xv), _rank(yv)))
        spread, selected = _top_bottom_spread(xv, yv, mask.nonzero().flatten().tolist())
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
        "cost_adjusted_score": _finite(score - 0.01),
        "capacity_adjusted_score": _finite(score - 0.01),
        "risk_adjusted_score": _finite(score / (1.0 + abs(rank_std))),
        "drawdown": _finite(min(losses) if losses else 0.0),
        "max_single_window_loss": _finite(min(losses) if losses else 0.0),
        "window_pass_ratio": _finite(sum(value >= 0 for value in spreads) / len(spreads) if spreads else 0.0),
        "stability_score": _finite(1.0 / (1.0 + rank_std)),
        "n_dates": float(len(rank_ics)),
        "n_observations": float(obs),
    }


def _empty_metrics() -> dict[str, float]:
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
    return {key: 0.0 for key in keys}


def _rank(values: torch.Tensor) -> torch.Tensor:
    order = torch.argsort(values)
    ranks = torch.zeros_like(values, dtype=torch.float32)
    ranks[order] = torch.arange(len(values), dtype=torch.float32)
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
