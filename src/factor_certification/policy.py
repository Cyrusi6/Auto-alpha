"""Certification policy loading and profiles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import CertificationPolicy


def policy_profile(name: str) -> CertificationPolicy:
    if name == "production_strict":
        return _with_hash(
            CertificationPolicy(
                policy_id="",
                profile_name=name,
                require_data_freeze=True,
                require_pit_passed=True,
                require_leakage_passed=True,
                require_alpha_lineage=True,
                max_pbo=0.25,
                min_deflated_ic_score=0.0,
                min_window_pass_ratio=0.55,
                min_placebo_percentile=0.75,
                max_null_exceedance_ratio=0.25,
                max_train_test_decay=0.50,
                min_regime_pass_ratio=0.60,
            )
        )
    if name == "research_standard":
        return _with_hash(
            CertificationPolicy(
                policy_id="",
                profile_name=name,
                require_data_freeze=True,
                require_pit_passed=False,
                require_leakage_passed=True,
                max_pbo=0.50,
                min_deflated_ic_score=-0.05,
                min_window_pass_ratio=0.40,
                min_placebo_percentile=0.60,
                max_null_exceedance_ratio=0.50,
                max_train_test_decay=1.00,
                min_regime_pass_ratio=0.40,
            )
        )
    return _with_hash(
        CertificationPolicy(
            policy_id="",
            profile_name="sample_lenient_certification",
            require_data_freeze=False,
            require_pit_passed=False,
            require_leakage_passed=False,
            require_alpha_lineage=False,
            max_pbo=1.0,
            min_deflated_ic_score=-999.0,
            min_out_of_sample_score=-999.0,
            min_window_pass_ratio=0.0,
            min_placebo_percentile=0.0,
            max_null_exceedance_ratio=1.0,
            max_train_test_decay=999.0,
            min_regime_pass_ratio=0.0,
        )
    )


def load_certification_policy(path: str | Path | None = None, profile_name: str = "sample_lenient_certification") -> CertificationPolicy:
    if path:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload.setdefault("policy_id", "")
        payload.setdefault("profile_name", profile_name)
        return _with_hash(CertificationPolicy(**payload))
    return policy_profile(profile_name)


def policy_hash(policy: CertificationPolicy) -> str:
    payload = policy.to_dict()
    payload = {key: value for key, value in payload.items() if key != "policy_id"}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _with_hash(policy: CertificationPolicy) -> CertificationPolicy:
    from dataclasses import replace

    return replace(policy, policy_id=f"cert_policy_{policy_hash(policy)}")
