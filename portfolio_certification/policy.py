"""Portfolio certification policy profiles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from .models import PortfolioCertificationPolicy


def portfolio_certification_policy_profile(profile_name: str = "sample_lenient_portfolio") -> PortfolioCertificationPolicy:
    if profile_name == "research_standard_portfolio":
        profile_name = "research_standard"
    if profile_name == "production_strict_portfolio":
        profile_name = "production_strict"
    if profile_name == "production_strict":
        return _with_hash(
            PortfolioCertificationPolicy(
                policy_id="",
                profile_name=profile_name,
                require_factor_certification=True,
                min_selection_score=-10.0,
                min_scenario_pass_ratio=0.75,
                min_successful_trial_count=2,
                min_fill_rate=0.5,
                max_constraint_reject_rate=0.5,
                max_avg_turnover=1.0,
                max_tracking_error=1.0,
                max_capacity_warning_count=10,
                max_risk_constraint_violations=10.0,
            )
        )
    if profile_name == "research_standard":
        return _with_hash(
            PortfolioCertificationPolicy(
                policy_id="",
                profile_name=profile_name,
                min_selection_score=-100.0,
                min_scenario_pass_ratio=0.5,
                min_successful_trial_count=1,
                min_fill_rate=0.0,
                max_constraint_reject_rate=1.0,
            )
        )
    return _with_hash(
        PortfolioCertificationPolicy(
            policy_id="",
            profile_name="sample_lenient_portfolio",
            require_factor_certification=False,
            min_selection_score=-999.0,
            min_scenario_pass_ratio=0.0,
            min_successful_trial_count=1,
            min_fill_rate=0.0,
            max_constraint_reject_rate=1.0,
            max_avg_turnover=999.0,
            max_tracking_error=999.0,
            max_capacity_warning_count=999,
            max_risk_constraint_violations=999.0,
        )
    )


def load_portfolio_certification_policy(
    path: str | Path | None = None,
    profile_name: str = "sample_lenient_portfolio",
) -> PortfolioCertificationPolicy:
    if path:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload.setdefault("policy_id", "")
        payload.setdefault("profile_name", profile_name)
        allowed = {key: value for key, value in payload.items() if key in PortfolioCertificationPolicy.__dataclass_fields__}
        return _with_hash(PortfolioCertificationPolicy(**allowed))
    return portfolio_certification_policy_profile(profile_name)


def portfolio_certification_policy_hash(policy: PortfolioCertificationPolicy) -> str:
    payload = {key: value for key, value in policy.to_dict().items() if key != "policy_id"}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _with_hash(policy: PortfolioCertificationPolicy) -> PortfolioCertificationPolicy:
    return replace(policy, policy_id=f"portfolio_cert_policy_{portfolio_certification_policy_hash(policy)}")
