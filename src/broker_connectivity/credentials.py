"""Credential reference collection and redaction helpers."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from artifact_schema.writer import write_json_artifact

from .models import BrokerConnectionProfile, BrokerCredentialRef


def redact_secret(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) <= 4:
        return "***"
    return f"{text[:2]}***{text[-2:]}"


def hash_prefix(value: str | None, length: int = 12) -> str:
    if not value:
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:length]


def collect_credential_refs(profile: BrokerConnectionProfile) -> list[BrokerCredentialRef]:
    refs: list[BrokerCredentialRef] = []
    for ref in profile.credential_refs:
        value = os.getenv(ref.env_var) if ref.env_var else None
        refs.append(
            BrokerCredentialRef(
                ref_id=ref.ref_id,
                name=ref.name,
                env_var=ref.env_var,
                required=ref.required,
                secret_type=ref.secret_type,
                redaction_hint=ref.redaction_hint or "redacted",
                present=bool(value),
                hash_prefix=hash_prefix(value),
                metadata={**ref.metadata, "redacted": redact_secret(value) if value else ""},
            )
        )
    return refs


def validate_credentials(profile: BrokerConnectionProfile, require_all: bool = False) -> tuple[bool, list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    for ref in collect_credential_refs(profile):
        if (require_all or ref.required) and not ref.present:
            issues.append(
                {
                    "severity": "error",
                    "code": "missing_credential_ref",
                    "message": f"required credential environment variable is not present: {ref.env_var}",
                    "env_var": ref.env_var,
                }
            )
    return not issues, issues


def write_credential_ref_manifest(path: str | Path, profile: BrokerConnectionProfile) -> Path:
    refs = collect_credential_refs(profile)
    payload = {
        "profile_id": profile.profile_id,
        "profile_name": profile.profile_name,
        "broker_name": profile.broker_name,
        "credential_refs": [ref.to_dict() for ref in refs],
        "secret_values_stored": False,
        "summary": {
            "credential_ref_count": len(refs),
            "present_count": sum(1 for ref in refs if ref.present),
            "missing_required_count": sum(1 for ref in refs if ref.required and not ref.present),
            "secret_blocker_count": 0,
        },
    }
    return write_json_artifact(path, payload, "broker_credential_ref_manifest", "broker_connectivity")

