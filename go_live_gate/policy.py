"""Go-live gate policy profiles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import GoLiveGatePolicy


def build_go_live_policy(profile_name: str = "sample_lenient_go_live") -> GoLiveGatePolicy:
    base = {
        "policy_id": f"go_live_policy_{profile_name}",
        "profile_name": profile_name,
        "metadata": {"real_submit_status_guard": True},
    }
    if profile_name == "sample_lenient_go_live":
        return GoLiveGatePolicy(**base, require_compliance_pack=True, require_broker_uat=True, require_risk_controls=False, require_kill_switch_available=False)
    if profile_name == "broker_uat_standard":
        return GoLiveGatePolicy(**base, require_compliance_pack=True, require_broker_uat=True, require_secret_scan_clean=True, require_risk_controls=True)
    if profile_name == "file_outbox_dryrun_standard":
        return GoLiveGatePolicy(
            **base,
            require_compliance_pack=True,
            require_broker_uat=True,
            require_file_outbox_dry_run=True,
            require_mapping_certification=True,
            require_operator_handoff=True,
            require_secret_scan_clean=True,
            require_risk_controls=True,
            require_kill_switch_available=True,
        )
    if profile_name == "manual_pilot_review_strict":
        return GoLiveGatePolicy(
            **base,
            require_data_freeze=True,
            require_factor_certification=True,
            require_portfolio_certification=True,
            require_shadow_replay=True,
            require_paper_replay=True,
            require_file_outbox_dry_run=True,
            require_mapping_certification=True,
            require_operator_handoff=True,
            require_compliance_pack=True,
            require_broker_uat=True,
            require_secret_scan_clean=True,
            require_no_open_critical_incidents=True,
            require_kill_switch_available=True,
            require_risk_controls=True,
            require_eod_reconciliation=True,
            require_release_gate=True,
            min_shadow_days=1,
            min_paper_days=1,
            min_file_outbox_dryrun_days=1,
        )
    raise ValueError(f"unknown go-live policy profile: {profile_name}")


def load_go_live_policy(path: str | Path) -> GoLiveGatePolicy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed = set(GoLiveGatePolicy.__dataclass_fields__)
    return GoLiveGatePolicy(**{key: payload[key] for key in allowed if key in payload})


def policy_hash(policy: GoLiveGatePolicy) -> str:
    text = json.dumps(policy.to_dict(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
