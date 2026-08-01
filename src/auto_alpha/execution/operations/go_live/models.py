"""Dataclasses for pre-live Go/No-Go review gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class GoLiveGateStatus:
    not_ready = "not_ready"
    ready_for_broker_uat = "ready_for_broker_uat"
    ready_for_file_outbox_dry_run = "ready_for_file_outbox_dry_run"
    ready_for_manual_pilot_review = "ready_for_manual_pilot_review"
    insufficient_data = "insufficient_data"


@dataclass(frozen=True)
class GoLiveGatePolicy:
    policy_id: str
    profile_name: str
    require_data_freeze: bool = False
    require_factor_certification: bool = False
    require_portfolio_certification: bool = False
    require_shadow_replay: bool = False
    require_paper_replay: bool = False
    require_file_outbox_dry_run: bool = False
    require_mapping_certification: bool = False
    require_operator_handoff: bool = False
    require_compliance_pack: bool = True
    require_broker_uat: bool = True
    require_broker_connectivity_for_broker_uat: bool = False
    require_readonly_mirror_for_manual_pilot_review: bool = False
    require_secret_scan_clean: bool = True
    require_no_open_critical_incidents: bool = True
    require_kill_switch_available: bool = True
    require_risk_controls: bool = True
    require_eod_reconciliation: bool = False
    require_release_gate: bool = False
    min_shadow_days: int = 0
    min_paper_days: int = 0
    min_file_outbox_dryrun_days: int = 0
    max_unresolved_incidents: int = 0
    max_secret_blockers: int = 0
    max_uat_failed_scenarios: int = 0
    max_connectivity_secret_blocker_count: int = 0
    max_readonly_mirror_break_count: int = 0
    max_file_roundtrip_errors: int = 0
    max_eod_breaks: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GoLiveGateCheck:
    check_id: str
    status: str
    severity: str
    value: Any
    threshold: Any
    reason: str
    artifact_refs: dict[str, str] = field(default_factory=dict)
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GoLiveGateScorecard:
    status: str
    score: float
    created_at: str
    policy: dict[str, Any]
    checks: list[GoLiveGateCheck]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": float(self.score),
            "created_at": self.created_at,
            "policy": dict(self.policy),
            "checks": [check.to_dict() for check in self.checks],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class GoLiveGateDecision:
    status: str
    passed: bool
    created_at: str
    score: float
    reasons: list[str]
    required_remediation: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    policy: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GoLiveReviewPackage:
    review_id: str
    created_at: str
    go_live_gate_decision_path: str
    go_live_status: str
    status: str
    reviewer: str | None
    comment: str | None
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
