"""Fail-closed bridge for validation stress reruns.

Validation Lab must not synthesize returns, fill rates, drawdowns, or costs from
an existing metric summary.  Callers that need stress evidence must provide an
actual simulator rerun callback which returns one independently computed result
per scenario.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import StressBacktestResult


class UnsupportedStressBacktestError(RuntimeError):
    """Raised when the formal path has no real simulator rerun implementation."""


SimulatorRerun = Callable[[str, dict[str, Any]], StressBacktestResult]


def run_stress_backtest_bundle(
    base_metrics: dict[str, float],
    cost_multipliers: list[float] | None = None,
    participations: list[float] | None = None,
    settlement_profiles: list[str] | None = None,
    top_n_values: list[int] | None = None,
    max_weight_values: list[float] | None = None,
    *,
    simulator_rerun: SimulatorRerun | None = None,
) -> tuple[list[StressBacktestResult], dict[str, Any]]:
    """Run independent simulator scenarios or fail closed.

    ``base_metrics`` remains in the signature for API compatibility, but is
    deliberately not read.  It cannot be used to manufacture scenario returns.
    """

    del base_metrics
    if simulator_rerun is None:
        raise UnsupportedStressBacktestError(
            "stress_backtest_unsupported_without_actual_simulator_rerun"
        )

    scenarios: list[tuple[str, dict[str, Any]]] = [("base", {})]
    scenarios.extend(
        ("modeled_cost", {"cost_multiplier": float(value)})
        for value in (cost_multipliers or [2.0])
    )
    scenarios.extend(
        ("participation", {"max_participation": float(value)})
        for value in (participations or [0.05])
    )
    scenarios.extend(
        ("settlement", {"settlement_profile": str(profile)})
        for profile in (settlement_profiles or ["conservative_t_plus_one_cash"])
    )
    scenarios.extend(("top_n", {"top_n": int(value)}) for value in (top_n_values or []))
    scenarios.extend(
        ("max_weight", {"max_weight": float(value)})
        for value in (max_weight_values or [])
    )

    results: list[StressBacktestResult] = []
    for index, (name, parameters) in enumerate(scenarios):
        scenario_id = f"{name}_{index}"
        result = simulator_rerun(scenario_id, dict(parameters))
        if not isinstance(result, StressBacktestResult):
            raise TypeError("simulator_rerun_must_return_stress_backtest_result")
        if result.scenario_id != scenario_id or result.parameters != parameters:
            raise RuntimeError("simulator_rerun_scenario_identity_mismatch")
        results.append(result)

    pass_ratio = sum(item.passed for item in results) / len(results) if results else 0.0
    return results, {
        "status": "completed_from_actual_simulator_reruns",
        "evidence_level": "simulator_rerun",
        "stress_scenario_count": len(results),
        "stress_backtest_pass_ratio": float(pass_ratio),
        "synthetic_metric_adjustment_used": False,
    }
