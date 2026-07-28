"""Factor admission gate for A-share factor research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from factor_store.lifecycle import FactorLifecycleStatus


@dataclass(frozen=True)
class FactorGateConfig:
    min_coverage: float = 0.8
    min_test_rank_ic_ir: float = 0.0
    min_test_score: float = 0.0
    min_test_evaluable_dates: int = 1
    min_test_valid_observations: int = 2
    max_turnover: float = 1.0
    max_abs_correlation: float = 0.95
    require_positive_test_rank_ic: bool = True


@dataclass(frozen=True)
class FactorGateDecision:
    passed: bool
    status: str
    reasons: list[str]
    checks: dict[str, float | bool | str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_factor_gate(
    metrics_by_split: dict[str, dict[str, float]],
    max_abs_corr: float,
    config: FactorGateConfig,
) -> FactorGateDecision:
    test_metrics = metrics_by_split.get("test", {})
    checks: dict[str, float | bool | str] = {
        "coverage": _finite(test_metrics.get("coverage")),
        "test_rank_ic_mean": float(test_metrics.get("rank_ic_mean", 0.0)),
        "test_rank_ic_ir": float(test_metrics.get("rank_ic_ir", 0.0)),
        "test_score": float(test_metrics.get("score", 0.0)),
        "test_evaluable_date_count": _finite(test_metrics.get("evaluable_date_count")),
        "test_valid_observation_count": _finite(test_metrics.get("valid_observation_count")),
        "turnover": _finite(test_metrics.get("turnover")),
        "max_abs_correlation": float(max_abs_corr),
        "require_positive_test_rank_ic": bool(config.require_positive_test_rank_ic),
    }

    reasons: list[str] = []
    if float(checks["coverage"]) < config.min_coverage:
        reasons.append("coverage_below_threshold")
    if float(checks["test_rank_ic_ir"]) < config.min_test_rank_ic_ir:
        reasons.append("test_rank_ic_ir_below_threshold")
    if float(checks["test_score"]) < config.min_test_score:
        reasons.append("test_score_below_threshold")
    if float(checks["test_evaluable_date_count"]) < config.min_test_evaluable_dates:
        reasons.append("test_evaluable_dates_below_threshold")
    if float(checks["test_valid_observation_count"]) < config.min_test_valid_observations:
        reasons.append("test_valid_observations_below_threshold")
    if float(checks["turnover"]) > config.max_turnover:
        reasons.append("turnover_above_threshold")
    if float(checks["max_abs_correlation"]) > config.max_abs_correlation:
        reasons.append("correlation_above_threshold")
    if config.require_positive_test_rank_ic and float(checks["test_rank_ic_mean"]) <= 0:
        reasons.append("test_rank_ic_not_positive")

    checks["oos_evidence_positive"] = bool(
        float(checks["test_evaluable_date_count"]) >= config.min_test_evaluable_dates
        and float(checks["test_valid_observation_count"]) >= config.min_test_valid_observations
        and float(checks["test_rank_ic_mean"]) > 0.0
        and float(checks["test_rank_ic_ir"]) >= config.min_test_rank_ic_ir
        and float(checks["test_score"]) >= config.min_test_score
    )
    if not checks["oos_evidence_positive"] and "test_rank_ic_not_positive" not in reasons:
        reasons.append("positive_oos_evidence_missing")

    passed = not reasons
    return FactorGateDecision(
        passed=passed,
        status=(
            FactorLifecycleStatus.validation_candidate.value
            if passed
            else FactorLifecycleStatus.research_rejected.value
        ),
        reasons=reasons,
        checks=checks,
    )


def _finite(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if math.isfinite(numeric) else 0.0
