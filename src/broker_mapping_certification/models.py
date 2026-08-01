"""Dataclasses for broker file mapping certification."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class BrokerMappingCertificationStatus:
    certified_for_dry_run = "certified_for_dry_run"
    conditional = "conditional"
    rejected = "rejected"
    insufficient_data = "insufficient_data"


@dataclass(frozen=True)
class BrokerMappingCertificationPolicy:
    policy_name: str
    max_roundtrip_errors: int = 0
    max_missing_ack: int = 0
    max_orphan_fills: int = 0
    require_qmt_skeleton_notice: bool = True
    allow_conditional: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerMappingCertificationDecision:
    certification_id: str
    created_at: str
    status: str
    profile_id: str
    schema_name: str
    policy_name: str
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    qmt_skeleton_notice: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerMappingCertificationPackage:
    certification_id: str
    decision: BrokerMappingCertificationDecision
    policy: BrokerMappingCertificationPolicy
    paths: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
