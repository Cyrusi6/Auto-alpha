"""Versioned policies for real long-history engineering validation."""

from __future__ import annotations

import hashlib
import json
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
    min_valid_oos_ratio: float = 0.90
    min_valid_oos_dates: int = 114
    min_evaluable_windows: int = 3
    min_cumulative_oos_dates: int = 342
    parameters_locked: bool = False

    def to_dict(self):
        return asdict(self)

    @property
    def policy_hash(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def validate_window_parameters(self, train_size: int, validation_size: int, test_size: int, step_size: int) -> None:
        if not self.parameters_locked:
            return
        actual = (int(train_size), int(validation_size), int(test_size), int(step_size))
        expected = (self.train_size, self.validation_size, self.test_size, self.step_size)
        if actual != expected:
            raise ValueError(f"production_policy_parameter_override:{actual}!={expected}")


POLICIES = {
    "real_long_history_engineering_robustness_v1": EngineeringRobustnessPolicy(),
    "real_long_history_engineering_robustness_v2": EngineeringRobustnessPolicy(
        policy_id="real_long_history_engineering_robustness_v2",
    ),
    "task054_production_engineering_v1": EngineeringRobustnessPolicy(
        policy_id="task054_production_engineering_v1",
        parameters_locked=True,
    ),
}


def load_validation_policy(policy_id: str | None) -> EngineeringRobustnessPolicy:
    key = policy_id or "real_long_history_engineering_robustness_v2"
    if key not in POLICIES:
        raise ValueError(f"unknown validation policy: {key}")
    return POLICIES[key]
