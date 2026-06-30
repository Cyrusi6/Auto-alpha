"""Dataclasses for live readiness gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class LiveReadinessStatus:
    ready_for_shadow = "ready_for_shadow"
    ready_for_paper_simulated = "ready_for_paper_simulated"
    ready_for_file_outbox_dry_run = "ready_for_file_outbox_dry_run"
    not_ready = "not_ready"
    insufficient_data = "insufficient_data"


@dataclass(frozen=True)
class LiveReadinessPolicy:
    policy_id: str
    profile: str
    min_replay_days: int = 1
    max_failed_replay_days: int = 0
    max_blocked_replay_days: int = 0
    min_shadow_days: int = 1
    max_shadow_drift: float = 0.10
    min_average_fill_rate: float = 0.0
    max_order_rejection_rate: float = 1.0
    max_incident_error_count: int = 999
    require_certified_factor: bool = False
    require_certified_portfolio: bool = False
    require_data_freeze: bool = False
    require_risk_controls: bool = False
    allow_file_outbox_only: bool = True
    require_broker_mapping_certification: bool = False
    require_broker_file_gateway_roundtrip: bool = False
    require_operator_handoff: bool = False
    require_go_live_gate: bool = False
    min_file_outbox_replay_days: int = 0
    max_file_roundtrip_errors: int = 0
    max_missing_handoff_items: int = 0
    require_no_real_submit: bool = True
    weights: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveReadinessCheck:
    check_id: str
    status: str
    score: float
    reason: str
    required: bool = True
    value: Any = None
    threshold: Any = None
    remediation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveReadinessScorecard:
    status: str
    score: float
    created_at: str
    policy: dict[str, Any]
    checks: list[LiveReadinessCheck]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": float(self.score),
            "created_at": self.created_at,
            "policy": dict(self.policy),
            "checks": [item.to_dict() for item in self.checks],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class LiveReadinessDecision:
    status: str
    passed: bool
    new_status: str
    created_at: str
    score: float
    reasons: list[str]
    required_remediation: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    policy: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
