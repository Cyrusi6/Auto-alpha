"""Placebo and null tests for selected factors."""

from __future__ import annotations

import random

import torch

from .metrics import _metrics_for_dates
from .models import PlaceboTestResult


def run_placebo_tests(
    factor_id: str,
    factors: torch.Tensor,
    target_ret: torch.Tensor,
    trade_dates: list[str],
    candidate_score: float,
    n_trials: int = 20,
    seed: int = 42,
) -> tuple[PlaceboTestResult, list[dict]]:
    rng = random.Random(seed)
    date_index = {date: idx for idx, date in enumerate(trade_dates)}
    n_trials = max(1, int(n_trials))
    scores: list[float] = []
    rows: list[dict] = []
    for trial in range(n_trials):
        mode = ["random_label_test", "time_permutation_test", "cross_section_permutation_test", "sign_flip_test"][trial % 4]
        permuted_factor = factors.clone()
        permuted_target = target_ret.clone()
        if mode == "random_label_test":
            flat = permuted_target.flatten()
            order = torch.randperm(flat.numel(), generator=torch.Generator().manual_seed(rng.randrange(1_000_000)))
            permuted_target = flat[order].reshape_as(permuted_target)
        elif mode == "time_permutation_test" and permuted_target.shape[1] > 1:
            order = torch.randperm(permuted_target.shape[1], generator=torch.Generator().manual_seed(rng.randrange(1_000_000)))
            permuted_target = permuted_target[:, order]
        elif mode == "cross_section_permutation_test" and permuted_factor.shape[0] > 1:
            for date_idx in range(permuted_factor.shape[1]):
                order = torch.randperm(permuted_factor.shape[0], generator=torch.Generator().manual_seed(rng.randrange(1_000_000)))
                permuted_factor[:, date_idx] = permuted_factor[order, date_idx]
        elif mode == "sign_flip_test":
            permuted_factor = -permuted_factor
        metrics = _metrics_for_dates(permuted_factor, permuted_target, trade_dates, date_index)
        score = float(metrics.get("out_of_sample_score", 0.0) or 0.0)
        scores.append(score)
        rows.append({"trial": trial, "mode": mode, "score": score, "metrics": metrics})
    exceed = sum(score >= candidate_score for score in scores)
    percentile = sum(score <= candidate_score for score in scores) / len(scores)
    result = PlaceboTestResult(
        factor_id=factor_id,
        n_trials=n_trials,
        candidate_score=float(candidate_score),
        placebo_score_distribution=[float(value) for value in scores],
        candidate_vs_placebo_percentile=float(percentile),
        placebo_passed=percentile >= 0.5,
        null_exceedance_count=int(exceed),
        null_exceedance_ratio=float(exceed / len(scores)),
        warnings=[] if len(trade_dates) > 1 else ["insufficient_dates_for_rich_placebo"],
    )
    return result, rows
