"""Policy profiles for live readiness."""

from __future__ import annotations

from .models import LiveReadinessPolicy


def build_policy(profile: str = "sample_lenient_readiness") -> LiveReadinessPolicy:
    profiles = {
        "sample_lenient_readiness": LiveReadinessPolicy(
            policy_id="sample_lenient_readiness_v1",
            profile="sample_lenient_readiness",
            min_replay_days=1,
            max_shadow_drift=1.0,
            min_average_fill_rate=0.0,
            max_order_rejection_rate=1.0,
            weights={"replay": 0.35, "shadow": 0.35, "operations": 0.30},
        ),
        "shadow_standard": LiveReadinessPolicy(
            policy_id="shadow_standard_v1",
            profile="shadow_standard",
            min_replay_days=5,
            min_shadow_days=5,
            max_shadow_drift=0.05,
            min_average_fill_rate=0.80,
            max_order_rejection_rate=0.20,
            weights={"replay": 0.35, "shadow": 0.45, "operations": 0.20},
        ),
        "paper_standard": LiveReadinessPolicy(
            policy_id="paper_standard_v1",
            profile="paper_standard",
            min_replay_days=10,
            min_shadow_days=5,
            max_shadow_drift=0.05,
            min_average_fill_rate=0.90,
            max_order_rejection_rate=0.10,
            require_certified_factor=True,
            require_certified_portfolio=True,
            require_data_freeze=True,
            require_risk_controls=True,
            weights={"replay": 0.30, "shadow": 0.35, "operations": 0.35},
        ),
        "file_outbox_dry_run_strict": LiveReadinessPolicy(
            policy_id="file_outbox_dry_run_strict_v1",
            profile="file_outbox_dry_run_strict",
            min_replay_days=10,
            min_shadow_days=10,
            max_shadow_drift=0.03,
            min_average_fill_rate=0.95,
            max_order_rejection_rate=0.05,
            require_certified_factor=True,
            require_certified_portfolio=True,
            require_data_freeze=True,
            require_risk_controls=True,
            allow_file_outbox_only=True,
            weights={"replay": 0.30, "shadow": 0.40, "operations": 0.30},
        ),
    }
    return profiles.get(profile, profiles["sample_lenient_readiness"])
