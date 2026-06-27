"""Factor admission gate for A-share factor research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FactorGateConfig:
    min_coverage: float = 0.8
    min_test_rank_ic_ir: float = -999.0
    min_test_score: float = -999.0
    max_turnover: float = 1.0
    max_abs_correlation: float = 0.95
    require_positive_test_rank_ic: bool = False


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
    all_metrics = metrics_by_split.get("all", {})
    test_metrics = metrics_by_split.get("test", {})
    checks: dict[str, float | bool | str] = {
        "coverage": float(all_metrics.get("coverage", 0.0)),
        "test_rank_ic_mean": float(test_metrics.get("rank_ic_mean", 0.0)),
        "test_rank_ic_ir": float(test_metrics.get("rank_ic_ir", 0.0)),
        "test_score": float(test_metrics.get("score", 0.0)),
        "turnover": float(all_metrics.get("turnover", 0.0)),
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
    if float(checks["turnover"]) > config.max_turnover:
        reasons.append("turnover_above_threshold")
    if float(checks["max_abs_correlation"]) > config.max_abs_correlation:
        reasons.append("correlation_above_threshold")
    if config.require_positive_test_rank_ic and float(checks["test_rank_ic_mean"]) <= 0:
        reasons.append("test_rank_ic_not_positive")

    passed = not reasons
    return FactorGateDecision(
        passed=passed,
        status="approved" if passed else "rejected",
        reasons=reasons,
        checks=checks,
    )
