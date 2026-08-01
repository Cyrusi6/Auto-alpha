"""Built-in read-only broker connectivity profiles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import (
    BrokerConnectionProfile,
    BrokerConnectivityMode,
    BrokerCredentialRef,
    PROHIBITED_METHODS,
)

READONLY_METHODS = [
    "ping",
    "get_server_time",
    "get_account_snapshot",
    "list_positions",
    "list_orders",
    "list_fills",
    "list_statements",
]


def build_broker_connection_profile(
    profile_name: str = "mock_readonly",
    *,
    broker_name: str | None = None,
    account_id: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> BrokerConnectionProfile:
    overrides = overrides or {}
    if profile_name == "mock_readonly":
        profile = BrokerConnectionProfile(
            profile_id="broker_profile_mock_readonly_v1",
            profile_name=profile_name,
            broker_name=broker_name or "mock_readonly_broker",
            connectivity_mode=BrokerConnectivityMode.offline_mock,
            endpoint_kind="mock",
            account_id_env_var="BROKER_UAT_ACCOUNT_ID",
            credential_refs=[],
            allowed_methods=READONLY_METHODS,
            readonly_methods=READONLY_METHODS,
            prohibited_methods=list(PROHIBITED_METHODS),
            notice="Offline deterministic read-only broker UAT fixture; no network, no credentials, no submit/cancel/replace.",
            metadata={"account_id": account_id or "paper_account", "real_submit_supported": False},
        )
    elif profile_name == "local_file_readonly_fixture":
        profile = BrokerConnectionProfile(
            profile_id="broker_profile_local_file_readonly_fixture_v1",
            profile_name=profile_name,
            broker_name=broker_name or "local_file_readonly_broker",
            connectivity_mode=BrokerConnectivityMode.local_file_fixture,
            endpoint_kind="local_file",
            account_id_env_var="BROKER_UAT_ACCOUNT_ID",
            credential_refs=[],
            allowed_methods=READONLY_METHODS,
            readonly_methods=READONLY_METHODS,
            prohibited_methods=list(PROHIBITED_METHODS),
            notice="Local file read-only fixture profile; no broker compatibility is implied.",
            metadata={"account_id": account_id or "paper_account", "real_submit_supported": False},
        )
    elif profile_name == "generic_http_readonly_skeleton":
        profile = BrokerConnectionProfile(
            profile_id="broker_profile_generic_http_readonly_skeleton_v1",
            profile_name=profile_name,
            broker_name=broker_name or "generic_readonly_broker",
            connectivity_mode=BrokerConnectivityMode.network_readonly_uat,
            endpoint_kind="generic_http_readonly",
            base_url_env_var="BROKER_UAT_BASE_URL",
            account_id_env_var="BROKER_UAT_ACCOUNT_ID",
            credential_refs=[BrokerCredentialRef("broker_uat_token", "Broker UAT token", "BROKER_UAT_TOKEN", required=True)],
            allowed_methods=READONLY_METHODS,
            readonly_methods=READONLY_METHODS,
            prohibited_methods=list(PROHIBITED_METHODS),
            notice="Generic HTTP read-only UAT skeleton. It does not imply broker compatibility and never supports submit/cancel/replace.",
            metadata={"account_id": account_id or "", "real_submit_supported": False, "endpoints": {}},
        )
    elif profile_name == "qmt_readonly_skeleton":
        profile = BrokerConnectionProfile(
            profile_id="broker_profile_qmt_readonly_skeleton_v1",
            profile_name=profile_name,
            broker_name=broker_name or "qmt_readonly_skeleton",
            connectivity_mode=BrokerConnectivityMode.network_readonly_uat,
            endpoint_kind="qmt_readonly_skeleton",
            account_id_env_var="BROKER_UAT_ACCOUNT_ID",
            credential_refs=[BrokerCredentialRef("qmt_readonly_ref", "QMT read-only credential reference", "BROKER_UAT_TOKEN", required=False)],
            allowed_methods=READONLY_METHODS,
            readonly_methods=READONLY_METHODS,
            prohibited_methods=list(PROHIBITED_METHODS),
            notice=(
                "QMT read-only UAT skeleton only. It does not guarantee real QMT or broker compatibility, "
                "does not include submit/cancel/replace, and requires manual verification of fields, paths, gateway, and authentication."
            ),
            metadata={"account_id": account_id or "", "real_submit_supported": False, "skeleton": True},
        )
    else:
        raise ValueError(f"unknown broker connectivity profile: {profile_name}")
    payload = profile.to_dict()
    payload.update(overrides)
    if "credential_refs" in payload:
        payload["credential_refs"] = [_credential_ref(item) for item in payload["credential_refs"]]
    return BrokerConnectionProfile(**payload)


def load_broker_connection_profile(path: str | Path) -> BrokerConnectionProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed = set(BrokerConnectionProfile.__dataclass_fields__)
    filtered = {key: payload[key] for key in allowed if key in payload}
    filtered["credential_refs"] = [_credential_ref(item) for item in filtered.get("credential_refs", [])]
    return BrokerConnectionProfile(**filtered)


def profile_hash(profile: BrokerConnectionProfile) -> str:
    payload = profile.to_dict()
    for ref in payload.get("credential_refs", []):
        ref.pop("present", None)
        ref.pop("hash_prefix", None)
        ref.pop("metadata", None)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _credential_ref(payload: BrokerCredentialRef | dict[str, Any]) -> BrokerCredentialRef:
    if isinstance(payload, BrokerCredentialRef):
        return payload
    allowed = set(BrokerCredentialRef.__dataclass_fields__)
    return BrokerCredentialRef(**{key: payload[key] for key in allowed if key in payload})

