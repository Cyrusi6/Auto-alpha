"""Locked contracts for factor-certified portfolio auto_alpha.research.discovery.studies; consolidated from auto_alpha.portfolio.construction.research."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


FACTOR_CERTIFIED_STATUS = "factor_certified"
SHADOW_CANDIDATE_STATUS = "shadow_candidate"
PORTFOLIO_REJECTED_STATUS = "portfolio_rejected"
DATA_BLOCKED_STATUS = "data_blocked"


class PortfolioResearchError(RuntimeError):
    """Raised when governed portfolio research must fail closed."""


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class StressScenario:
    scenario_id: str
    modeled_cost_multiplier: float = 1.0
    lagged_adv_multiplier: float = 1.0
    required_regime: str | None = None

    def __post_init__(self) -> None:
        if self.modeled_cost_multiplier <= 0.0 or self.lagged_adv_multiplier <= 0.0:
            raise ValueError("stress multipliers must be positive")


@dataclass(frozen=True)
class PortfolioResearchPolicy:
    policy_id: str = "factor_certified_portfolio_walk_forward_v1"
    train_size: int = 756
    validation_size: int = 126
    test_size: int = 126
    step_size: int = 126
    label_horizon: int = 2
    min_embargo: int = 2
    min_factor_count: int = 3
    min_family_count: int = 2
    min_cross_section_breadth: int = 30
    min_pair_observations: int = 252
    min_evaluable_windows: int = 3
    min_valid_test_dates: int = 114
    correlation_threshold: float = 0.70
    family_weight_cap: float = 0.50
    cluster_weight_cap: float = 0.60
    factor_weight_cap: float = 0.35
    weight_shrinkage: float = 0.25
    max_weight_change: float = 0.15
    min_positive_window_ratio: float = 0.60
    min_universe_pass_ratio: float = 1.0
    min_benchmark_pass_ratio: float = 1.0
    min_stress_pass_ratio: float = 1.0
    min_cost_adjusted_return: float = 0.0
    min_active_return: float = 0.0
    max_drawdown: float = 0.50
    top_n: int = 20
    max_stock_weight: float = 0.10
    initial_aum: float = 1_000_000.0
    lot_size: int = 100
    parameters_locked: bool = True
    certification_supported: bool = False
    shadow_only: bool = True
    paper_requires_independent_audit: bool = True
    required_scenarios: tuple[StressScenario, ...] = field(
        default_factory=lambda: (
            StressScenario("baseline"),
            StressScenario("double_modeled_cost", modeled_cost_multiplier=2.0),
            StressScenario("volume_down_50pct", lagged_adv_multiplier=0.50),
            StressScenario(
                "extreme_volatility",
                modeled_cost_multiplier=2.0,
                lagged_adv_multiplier=0.50,
                required_regime="extreme_volatility",
            ),
        )
    )

    def __post_init__(self) -> None:
        sizes = (self.train_size, self.test_size, self.step_size, self.label_horizon, self.min_embargo)
        if any(value < 1 for value in sizes) or self.validation_size < 0:
            raise ValueError("walk-forward sizes and horizon must be positive")
        if self.min_embargo < self.label_horizon:
            raise ValueError("portfolio embargo must cover label horizon")
        if self.min_factor_count < 2 or self.min_family_count < 1:
            raise ValueError("portfolio combination requires multiple certified factors")
        if self.min_cross_section_breadth < 2 or self.min_valid_test_dates < 1:
            raise ValueError("portfolio sample thresholds are invalid")
        bounded = (
            self.correlation_threshold,
            self.family_weight_cap,
            self.cluster_weight_cap,
            self.factor_weight_cap,
            self.weight_shrinkage,
            self.max_weight_change,
            self.min_positive_window_ratio,
            self.min_universe_pass_ratio,
            self.min_benchmark_pass_ratio,
            self.min_stress_pass_ratio,
            self.max_stock_weight,
        )
        if any(value < 0.0 or value > 1.0 for value in bounded):
            raise ValueError("portfolio policy ratios must be within [0, 1]")
        if not self.parameters_locked or self.certification_supported or not self.shadow_only:
            raise ValueError("production portfolio research must remain locked and shadow-only")
        scenario_ids = [scenario.scenario_id for scenario in self.required_scenarios]
        if scenario_ids != ["baseline", "double_modeled_cost", "volume_down_50pct", "extreme_volatility"]:
            raise ValueError("production stress scenario set is immutable")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def policy_hash(self) -> str:
        return stable_hash(self.to_dict())

    def effective_embargo(self, max_factor_lookback: int) -> int:
        return max(int(max_factor_lookback), self.label_horizon, self.min_embargo)


def validate_production_policy(policy: PortfolioResearchPolicy) -> None:
    if policy.policy_id != "factor_certified_portfolio_walk_forward_v1":
        raise PortfolioResearchError("production_portfolio_policy_id_invalid")
    if not policy.parameters_locked or not policy.shadow_only or policy.certification_supported:
        raise PortfolioResearchError("production_portfolio_policy_boundary_invalid")
    if policy.min_embargo < policy.label_horizon:
        raise PortfolioResearchError("portfolio_embargo_shorter_than_label_horizon")
