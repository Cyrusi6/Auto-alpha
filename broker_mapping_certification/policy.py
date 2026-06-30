"""Certification policy profiles."""

from __future__ import annotations

import json
from pathlib import Path

from .models import BrokerMappingCertificationPolicy


def load_certification_policy(policy_name: str = "dry_run_standard", policy_config: str | Path | None = None) -> BrokerMappingCertificationPolicy:
    if policy_config is not None:
        payload = json.loads(Path(policy_config).read_text(encoding="utf-8"))
        return BrokerMappingCertificationPolicy(
            policy_name=str(payload.get("policy_name") or policy_name),
            max_roundtrip_errors=int(payload.get("max_roundtrip_errors", 0) or 0),
            max_missing_ack=int(payload.get("max_missing_ack", 0) or 0),
            max_orphan_fills=int(payload.get("max_orphan_fills", 0) or 0),
            require_qmt_skeleton_notice=bool(payload.get("require_qmt_skeleton_notice", True)),
            allow_conditional=bool(payload.get("allow_conditional", True)),
            metadata=dict(payload.get("metadata") or {}),
        )
    if policy_name == "sample_lenient_mapping":
        return BrokerMappingCertificationPolicy(policy_name=policy_name, max_roundtrip_errors=1, max_missing_ack=1, allow_conditional=True)
    if policy_name == "file_outbox_strict":
        return BrokerMappingCertificationPolicy(policy_name=policy_name, max_roundtrip_errors=0, max_missing_ack=0, max_orphan_fills=0, allow_conditional=False)
    return BrokerMappingCertificationPolicy(policy_name=policy_name)
