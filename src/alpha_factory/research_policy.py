"""Versioned two-stage Alpha Factory research policies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from evaluation import ObjectiveSpec


@dataclass(frozen=True)
class AlphaResearchPolicy:
    policy_id: str = "alpha_factory_two_stage_oos_v1"
    proxy_neutralization: str = "neutralize_industry_size"
    proxy_min_coverage: float = 0.50
    proxy_min_cross_section_breadth: int = 30
    proxy_min_evaluable_dates: int = 20
    proxy_min_universe_count: int = 2
    proxy_max_abs_existing_correlation: float = 0.95
    train_size: int = 756
    validation_size: int = 126
    test_size: int = 126
    step_size: int = 126
    min_valid_oos_dates: int = 114
    min_evaluable_windows: int = 3
    min_cumulative_oos_dates: int = 342
    min_mean_rank_ic: float = 0.0
    min_mean_icir: float = 0.0
    min_window_pass_ratio: float = 0.50
    max_train_test_decay: float = 1.0
    placebo_trials: int = 40
    min_placebo_percentile: float = 0.80
    min_regime_pass_ratio: float = 0.50
    min_time_sensitivity_ratio: float = 0.50
    min_parameter_sensitivity_ratio: float = 0.67
    max_bh_q_value: float = 0.10
    max_selection_adjusted_p_value: float = 0.10
    max_pbo: float = 0.67
    max_abs_size_exposure: float = 0.50
    max_abs_beta_exposure: float = 0.50
    max_abs_liquidity_exposure: float = 0.50
    max_industry_concentration: float = 0.60
    modeled_cost_bps: float = 20.0
    capacity_participation: float = 0.10
    capacity_aum_cny: float = 1_000_000.0
    min_capacity_feasible_ratio: float = 0.90
    parameters_locked: bool = True
    certification_supported: bool = False
    proxy_objectives: tuple[ObjectiveSpec, ...] = field(
        default_factory=lambda: (
            ObjectiveSpec("neutralized_rank_ic_mean", 1, 2.0),
            ObjectiveSpec("ic_stability", 1, 1.0),
            ObjectiveSpec("coverage", 1, 1.0),
            ObjectiveSpec("turnover_proxy", -1, 0.75),
            ObjectiveSpec("complexity", -1, 0.50),
            ObjectiveSpec("lookback", -1, 0.50),
            ObjectiveSpec("max_abs_existing_correlation", -1, 1.0),
            ObjectiveSpec("family_novelty", 1, 0.75),
            ObjectiveSpec("universe_direction_consistency", 1, 1.0),
        )
    )
    full_objectives: tuple[ObjectiveSpec, ...] = field(
        default_factory=lambda: (
            ObjectiveSpec("mean_rank_ic", 1, 2.0),
            ObjectiveSpec("mean_icir", 1, 1.5),
            ObjectiveSpec("window_pass_ratio", 1, 1.0),
            ObjectiveSpec("stability_score", 1, 1.0),
            ObjectiveSpec("placebo_percentile", 1, 1.0),
            ObjectiveSpec("regime_pass_ratio", 1, 1.0),
            ObjectiveSpec("time_sensitivity_ratio", 1, 0.75),
            ObjectiveSpec("parameter_sensitivity_ratio", 1, 0.75),
            ObjectiveSpec("modeled_net_spread", 1, 0.75),
            ObjectiveSpec("capacity_feasible_ratio", 1, 0.75),
            ObjectiveSpec("max_style_exposure", -1, 0.75),
            ObjectiveSpec("pbo_estimate", -1, 1.0),
            ObjectiveSpec("bh_q_value", -1, 1.0),
        )
    )

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["proxy_objectives"] = [asdict(value) for value in self.proxy_objectives]
        payload["full_objectives"] = [asdict(value) for value in self.full_objectives]
        return payload

    @property
    def policy_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


POLICIES = {
    "alpha_factory_two_stage_oos_v1": AlphaResearchPolicy(),
    "alpha_factory_two_stage_smoke_v1": AlphaResearchPolicy(
        policy_id="alpha_factory_two_stage_smoke_v1",
        proxy_neutralization="raw",
        proxy_min_coverage=0.0,
        proxy_min_cross_section_breadth=2,
        proxy_min_evaluable_dates=1,
        proxy_min_universe_count=1,
        train_size=2,
        validation_size=1,
        test_size=1,
        step_size=1,
        min_valid_oos_dates=1,
        min_evaluable_windows=1,
        min_cumulative_oos_dates=1,
        placebo_trials=4,
        min_placebo_percentile=0.0,
        min_regime_pass_ratio=0.0,
        min_time_sensitivity_ratio=0.0,
        min_parameter_sensitivity_ratio=0.0,
        max_bh_q_value=1.0,
        max_selection_adjusted_p_value=1.0,
        max_pbo=1.0,
        max_abs_size_exposure=1.0,
        max_abs_beta_exposure=1.0,
        max_abs_liquidity_exposure=1.0,
        max_industry_concentration=1.0,
        min_capacity_feasible_ratio=0.0,
        parameters_locked=False,
    ),
}


def load_alpha_research_policy(policy_id: str | None, *, production_research: bool = False) -> AlphaResearchPolicy:
    key = policy_id or (
        "alpha_factory_two_stage_oos_v1"
        if production_research
        else "alpha_factory_two_stage_smoke_v1"
    )
    if key not in POLICIES:
        raise ValueError(f"unknown alpha research policy: {key}")
    if production_research and key != "alpha_factory_two_stage_oos_v1":
        raise ValueError("production research requires alpha_factory_two_stage_oos_v1")
    return POLICIES[key]
