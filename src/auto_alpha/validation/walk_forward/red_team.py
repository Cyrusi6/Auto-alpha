"""One-shot sealed-holdout validation for frozen A-share factor candidates."""

from auto_alpha.validation.walk_forward.red_team_candidate_pool import freeze_candidate_pool
from auto_alpha.validation.walk_forward.red_team_candidate_pool import validate_candidate_pool_manifest
from auto_alpha.validation.walk_forward.red_team_capability import HoldoutCapabilityRegistry
from auto_alpha.validation.walk_forward.red_team_contracts import HoldoutCalibrationProfile
from auto_alpha.validation.walk_forward.red_team_contracts import SealedHoldoutPolicy
from auto_alpha.validation.walk_forward.red_team_contracts import publish_holdout_policy
from auto_alpha.validation.walk_forward.red_team_contracts import validate_holdout_policy
from auto_alpha.validation.walk_forward.red_team_evaluator import ValidationRedTeamAgent
from auto_alpha.validation.walk_forward.red_team_evaluator import validate_sealed_holdout_view
from auto_alpha.validation.walk_forward.red_team_preflight import preflight_canonical_holdout
from auto_alpha.validation.walk_forward.red_team_verifier import verify_holdout_result

__all__ = [
    "HoldoutCalibrationProfile",
    "HoldoutCapabilityRegistry",
    "SealedHoldoutPolicy",
    "ValidationRedTeamAgent",
    "freeze_candidate_pool",
    "publish_holdout_policy",
    "preflight_canonical_holdout",
    "validate_candidate_pool_manifest",
    "validate_holdout_policy",
    "validate_sealed_holdout_view",
    "verify_holdout_result",
]
