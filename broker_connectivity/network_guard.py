"""Network and method guardrails for read-only broker UAT."""

from __future__ import annotations

import json
import os
from pathlib import Path

from approval import LocalApprovalStore
from artifact_schema.writer import write_json_artifact

from .models import (
    BrokerConnectionProfile,
    BrokerConnectivityBlockedError,
    BrokerConnectivityStatus,
    BrokerNetworkGuard,
)


def build_network_guard(
    profile: BrokerConnectionProfile,
    *,
    allow_network: bool = False,
    approval_store_dir: str | Path | None = None,
    approval_id: str | None = None,
    require_approval: bool = False,
) -> BrokerNetworkGuard:
    env_gate_name = "BROKER_UAT_ALLOW_NETWORK"
    env_gate_value = os.getenv(env_gate_name, "")
    network_profile = profile.connectivity_mode == "network_readonly_uat"
    approved = _approval_is_approved(approval_store_dir, approval_id) if require_approval else bool(approval_id)
    blocked: list[str] = []
    if network_profile:
        if not allow_network:
            blocked.append("cli_allow_network_missing")
        if env_gate_value != "1":
            blocked.append("env_gate_missing")
        if require_approval and not approved:
            blocked.append("broker_connectivity_review_not_approved")
    status = BrokerConnectivityStatus.blocked if blocked else BrokerConnectivityStatus.passed if network_profile else BrokerConnectivityStatus.skipped
    return BrokerNetworkGuard(
        allow_network=bool(allow_network),
        env_gate_name=env_gate_name,
        env_gate_value="1" if env_gate_value == "1" else "",
        approval_required=bool(require_approval),
        approval_id=approval_id,
        approved=bool(approved),
        readonly_only=True,
        blocked_reason=",".join(blocked),
        status=status,
    )


def enforce_readonly_method(profile: BrokerConnectionProfile, guard: BrokerNetworkGuard, method: str) -> None:
    if method in profile.prohibited_methods or method not in profile.readonly_methods:
        raise BrokerConnectivityBlockedError(f"broker connectivity method is prohibited or not read-only: {method}")
    if profile.connectivity_mode == "network_readonly_uat" and guard.status == BrokerConnectivityStatus.blocked:
        raise BrokerConnectivityBlockedError(f"broker readonly network is blocked: {guard.blocked_reason}")


def write_network_guard_report(path: str | Path, profile: BrokerConnectionProfile, guard: BrokerNetworkGuard) -> Path:
    payload = {
        "profile_id": profile.profile_id,
        "profile_name": profile.profile_name,
        "broker_name": profile.broker_name,
        "connectivity_mode": profile.connectivity_mode,
        "status": guard.status,
        "network_guard": guard.to_dict(),
        "readonly_only": True,
        "real_submit_supported": False,
        "prohibited_methods": list(profile.prohibited_methods),
        "summary": {
            "allow_network": guard.allow_network,
            "env_gate_set": guard.env_gate_value == "1",
            "approval_required": guard.approval_required,
            "approved": guard.approved,
            "blocked_reason": guard.blocked_reason,
        },
    }
    return write_json_artifact(path, payload, "broker_network_guard_report", "broker_connectivity")


def _approval_is_approved(approval_store_dir: str | Path | None, approval_id: str | None) -> bool:
    if not approval_store_dir or not approval_id:
        return False
    try:
        batch = LocalApprovalStore(approval_store_dir).load_batch(approval_id)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return False
    return batch.status == "approved" and batch.approval_type == "broker_connectivity_review"

