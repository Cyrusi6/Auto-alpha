"""Dataclasses for benchmark-aware portfolio optimization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class OptimizationConfig:
    objective: str = "alpha_risk"
    risk_aversion: float = 1.0
    turnover_penalty: float = 0.1
    benchmark_weight: float = 0.25
    max_weight: float = 0.10
    max_names: int = 20
    min_names: int = 1
    max_turnover: float = 1.00
    max_industry_active_weight: float = 0.20
    max_tracking_error: float = 1.00
    long_only: bool = True
    cash_weight: float = 0.0


@dataclass(frozen=True)
class OptimizationResult:
    weights: dict[str, float]
    objective_value: float
    predicted_alpha: float
    predicted_risk: float
    tracking_error: float
    turnover: float
    violations: list[str]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": {key: float(value) for key, value in self.weights.items()},
            "objective_value": float(self.objective_value),
            "predicted_alpha": float(self.predicted_alpha),
            "predicted_risk": float(self.predicted_risk),
            "tracking_error": float(self.tracking_error),
            "turnover": float(self.turnover),
            "violations": list(self.violations),
            "diagnostics": self.diagnostics,
        }


def to_jsonable_dataclass(record) -> dict[str, Any]:
    return asdict(record)
