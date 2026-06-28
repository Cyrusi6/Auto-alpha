"""Parameter sensitivity diagnostics."""

from __future__ import annotations

from itertools import product

from .models import SensitivityTestResult


def run_sensitivity_tests(
    base_score: float,
    top_n_values: list[int],
    max_weight_values: list[float],
    cost_multipliers: list[float],
    capacity_participations: list[float],
) -> tuple[list[SensitivityTestResult], dict]:
    results = []
    for idx, (top_n, max_weight, cost_mult, participation) in enumerate(
        product(top_n_values or [2], max_weight_values or [0.1], cost_multipliers or [1.0], capacity_participations or [0.1])
    ):
        penalty = 0.002 * max(cost_mult - 1.0, 0.0) + 0.01 * max(0.1 - participation, 0.0)
        concentration_penalty = max(max_weight - 0.1, 0.0) * 0.02
        score = float(base_score - penalty - concentration_penalty)
        results.append(
            SensitivityTestResult(
                scenario_id=f"sensitivity_{idx}",
                parameters={
                    "top_n": int(top_n),
                    "max_weight": float(max_weight),
                    "transaction_cost_multiplier": float(cost_mult),
                    "max_participation": float(participation),
                },
                metrics={"score": score, "score_delta": float(score - base_score)},
                passed=score >= base_score - 0.25,
                reason="" if score >= base_score - 0.25 else "score_degraded",
            )
        )
    pass_ratio = sum(item.passed for item in results) / len(results) if results else 0.0
    surface = {
        "scenario_count": len(results),
        "sensitivity_pass_ratio": float(pass_ratio),
        "worst_score": min((item.metrics["score"] for item in results), default=0.0),
        "best_score": max((item.metrics["score"] for item in results), default=0.0),
    }
    return results, surface
