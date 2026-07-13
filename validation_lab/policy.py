"""Versioned policies for real long-history engineering validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EngineeringRobustnessPolicy:
    policy_id: str = "real_long_history_engineering_robustness_v1"
    train_size: int = 756
    validation_size: int = 126
    test_size: int = 126
    step_size: int = 126
    min_cross_section_breadth: int = 30
    min_oos_dates: int = 126
    min_coverage: float = 0.05
    max_coverage: float = 1.0
    min_standard_deviation: float = 1e-8
    min_mean_rank_ic: float = 0.0
    min_mean_icir: float = 0.0
    min_window_pass_ratio: float = 0.5
    max_train_test_decay: float = 1.0
    certification_supported: bool = False

    def to_dict(self):
        return asdict(self)


POLICIES = {"real_long_history_engineering_robustness_v1": EngineeringRobustnessPolicy()}


def load_validation_policy(policy_id: str | None) -> EngineeringRobustnessPolicy:
    key = policy_id or "real_long_history_engineering_robustness_v1"
    if key not in POLICIES:
        raise ValueError(f"unknown validation policy: {key}")
    return POLICIES[key]
