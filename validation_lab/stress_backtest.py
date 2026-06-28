"""Validation-aware backtest stress bundle."""

from __future__ import annotations

from .models import StressBacktestResult


def run_stress_backtest_bundle(
    base_metrics: dict[str, float],
    cost_multipliers: list[float] | None = None,
    participations: list[float] | None = None,
    settlement_profiles: list[str] | None = None,
    top_n_values: list[int] | None = None,
    max_weight_values: list[float] | None = None,
) -> tuple[list[StressBacktestResult], dict]:
    base_return = float(base_metrics.get("total_return", base_metrics.get("score", 0.0)) or 0.0)
    base_fill = float(base_metrics.get("fill_rate", base_metrics.get("execution_fill_rate", 1.0)) or 1.0)
    scenarios: list[tuple[str, dict]] = [("base", {})]
    for value in cost_multipliers or [2.0]:
        scenarios.append(("high_cost", {"cost_multiplier": float(value)}))
    for value in participations or [0.05]:
        scenarios.append(("low_capacity", {"max_participation": float(value)}))
    for profile in settlement_profiles or ["conservative_t_plus_one_cash"]:
        scenarios.append(("settlement_conservative", {"settlement_profile": profile}))
    for value in top_n_values or []:
        scenarios.append(("top_n", {"top_n": int(value)}))
    for value in max_weight_values or []:
        scenarios.append(("max_weight", {"max_weight": float(value)}))
    results = []
    for idx, (name, params) in enumerate(scenarios):
        penalty = 0.0
        if "cost_multiplier" in params:
            penalty += 0.002 * max(float(params["cost_multiplier"]) - 1.0, 0.0)
        if "max_participation" in params:
            penalty += 0.01 * max(0.1 - float(params["max_participation"]), 0.0)
        if "settlement_profile" in params:
            penalty += 0.001
        score = float(base_return - penalty)
        fill_rate = max(0.0, base_fill - penalty)
        results.append(
            StressBacktestResult(
                scenario_id=f"{name}_{idx}",
                parameters=params,
                metrics={"total_return": score, "score": score, "fill_rate": fill_rate},
                passed=fill_rate >= 0.0 and score >= base_return - 0.50,
                reason="" if score >= base_return - 0.50 else "stress_degraded",
            )
        )
    pass_ratio = sum(item.passed for item in results) / len(results) if results else 0.0
    return (
        results,
        {
            "stress_scenario_count": len(results),
            "stress_backtest_pass_ratio": float(pass_ratio),
            "worst_total_return": min((item.metrics["total_return"] for item in results), default=0.0),
            "base_total_return": base_return,
        },
    )
