"""Overfit-risk diagnostics based on validation windows and trial counts."""

from __future__ import annotations

import math
from statistics import mean

from .models import FactorValidationWindowResult, MultipleTestingSummary, OverfitRiskSummary


def estimate_overfit_risk(
    window_results: list[FactorValidationWindowResult],
    multiple_testing: MultipleTestingSummary | None = None,
) -> OverfitRiskSummary:
    if len(window_results) < 2:
        score = window_results[0].test_metrics.get("out_of_sample_score", 0.0) if window_results else 0.0
        return OverfitRiskSummary(
            pbo_estimate=0.0,
            cscv_logit_values=[],
            in_sample_rank=1.0,
            out_sample_rank=1.0,
            degradation_ratio=0.0,
            overfit_risk_level="insufficient_data",
            deflated_sharpe_like_score=float(score),
            deflated_ic_like_score=float(score),
            selected_candidate_rank_stability=1.0,
            insufficient_data=True,
            approximate=True,
        )
    logits = []
    degradations = []
    for item in window_results:
        train = float(item.train_metrics.get("out_of_sample_score", 0.0) or 0.0)
        test = float(item.test_metrics.get("out_of_sample_score", 0.0) or 0.0)
        degradation = train - test
        degradations.append(degradation)
        denom = abs(train) + abs(test) + 1e-9
        rel = (test - train) / denom
        clipped = max(min(0.999, (rel + 1.0) / 2.0), 0.001)
        logits.append(math.log(clipped / (1.0 - clipped)))
    pbo = sum(value < 0 for value in logits) / len(logits)
    score = mean(float(item.test_metrics.get("out_of_sample_score", 0.0) or 0.0) for item in window_results)
    penalty = (multiple_testing.multiple_testing_penalty if multiple_testing else 0.0)
    deflated = float(score - penalty)
    risk = "low" if pbo < 0.34 else ("medium" if pbo < 0.67 else "high")
    return OverfitRiskSummary(
        pbo_estimate=float(pbo),
        cscv_logit_values=[float(value) for value in logits],
        in_sample_rank=1.0,
        out_sample_rank=float(1.0 + max(mean(degradations), 0.0)),
        degradation_ratio=float(mean(degradations)),
        overfit_risk_level=risk,
        deflated_sharpe_like_score=deflated,
        deflated_ic_like_score=deflated,
        selected_candidate_rank_stability=float(1.0 / (1.0 + abs(mean(degradations)))),
        insufficient_data=False,
        approximate=True,
    )
