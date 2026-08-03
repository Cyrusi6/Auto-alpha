"""Versioned, stratum-specific policies for one-shot sealed holdout auto_alpha.research.discovery.evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from auto_alpha.validation.lab.red_team_io import HoldoutContractError
from auto_alpha.validation.lab.red_team_io import publish_generation
from auto_alpha.validation.lab.red_team_io import stable_hash
from auto_alpha.validation.lab.red_team_io import read_json


@dataclass(frozen=True)
class HoldoutCalibrationProfile:
    universe_name: str
    holding_period_days: int
    neutralization_method: str
    rebalance_frequency: str
    window_size: int = 126
    min_cross_section_breadth: int = 30
    min_median_rank_ic: float = 0.0
    min_positive_rank_ic_window_ratio: float = 0.60
    min_walk_forward_pass_ratio: float = 0.60
    min_net_top_bottom_spread: float = 0.0
    max_existing_factor_correlation: float = 0.70
    min_regime_direction_ratio: float = 0.60
    min_universe_direction_ratio: float = 0.60
    min_placebo_percentile: float = 0.80
    modeled_cost_bps: float = 20.0
    placebo_trials: int = 40
    min_evaluable_windows: int = 3

    @property
    def calibration_key(self) -> str:
        return stable_hash(
            {
                "universe_name": self.universe_name,
                "holding_period_days": self.holding_period_days,
                "neutralization_method": self.neutralization_method,
                "rebalance_frequency": self.rebalance_frequency,
            }
        )


@dataclass(frozen=True)
class SealedHoldoutPolicy:
    policy_id: str
    profile: HoldoutCalibrationProfile
    parameters_locked: bool = True
    one_shot: bool = True
    failed_formula_reuse_forbidden: bool = True
    certification_supported: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "calibration_key": self.profile.calibration_key}

    @property
    def policy_hash(self) -> str:
        return stable_hash(self.to_dict())


def publish_holdout_policy(policy: SealedHoldoutPolicy, output_root: str | Path) -> tuple[Path, dict[str, Any]]:
    core = {
        "status": "locked",
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "policy": policy.to_dict(),
        "calibration_key": policy.profile.calibration_key,
        "parameters_locked": policy.parameters_locked,
        "one_shot": policy.one_shot,
        "certification_supported": False,
    }
    return publish_generation(
        output_root,
        generation_prefix="holdout_policy",
        manifest_name="sealed_holdout_policy.json",
        artifact_type="sealed_holdout_policy",
        producer="validation_red_team",
        core=core,
    )


def validate_holdout_policy(path: str | Path) -> tuple[SealedHoldoutPolicy, dict[str, Any]]:
    payload = read_json(path, artifact_type="sealed_holdout_policy")
    policy_payload = payload.get("policy")
    if not isinstance(policy_payload, dict) or not isinstance(policy_payload.get("profile"), dict):
        raise HoldoutContractError("holdout_policy_payload_missing")
    profile = HoldoutCalibrationProfile(**policy_payload["profile"])
    policy = SealedHoldoutPolicy(
        policy_id=str(policy_payload["policy_id"]),
        profile=profile,
        parameters_locked=bool(policy_payload.get("parameters_locked")),
        one_shot=bool(policy_payload.get("one_shot")),
        failed_formula_reuse_forbidden=bool(policy_payload.get("failed_formula_reuse_forbidden")),
        certification_supported=bool(policy_payload.get("certification_supported")),
    )
    if not policy.parameters_locked or not policy.one_shot or policy.certification_supported:
        raise HoldoutContractError("holdout_policy_boundary_invalid")
    if policy.profile.min_evaluable_windows < 1 or policy.profile.window_size < 1:
        raise HoldoutContractError("holdout_policy_window_contract_invalid")
    if payload.get("policy_hash") != policy.policy_hash or payload.get("calibration_key") != profile.calibration_key:
        raise HoldoutContractError("holdout_policy_hash_mismatch")
    expected_core = {
        "status": "locked",
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "policy": policy.to_dict(),
        "calibration_key": profile.calibration_key,
        "parameters_locked": True,
        "one_shot": True,
        "certification_supported": False,
    }
    if payload.get("content_hash") != stable_hash(expected_core):
        raise HoldoutContractError("holdout_policy_content_hash_mismatch")
    return policy, payload
