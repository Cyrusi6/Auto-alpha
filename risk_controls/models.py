"""Dataclasses for local pre-trade risk controls."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class RiskControlScope:
    order = "order"
    child_order = "child_order"
    broker_request = "broker_request"
    portfolio = "portfolio"
    account = "account"
    symbol = "symbol"
    kill_switch = "kill_switch"


class RiskControlSeverity:
    info = "info"
    warning = "warning"
    error = "error"
    blocker = "blocker"


class RiskBreachAction:
    allow = "allow"
    warn = "warn"
    clip = "clip"
    reject = "reject"
    block = "block"
    require_approval = "require_approval"


class RiskControlStatus:
    passed = "passed"
    warning = "warning"
    rejected = "rejected"
    clipped = "clipped"
    blocked = "blocked"
    override_required = "override_required"


@dataclass(frozen=True)
class RiskLimitDefinition:
    limit_id: str
    name: str
    scope: str
    metric: str
    threshold: float | str | bool | None
    action: str = RiskBreachAction.reject
    severity: str = RiskControlSeverity.error
    enabled: bool = True
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskControlPolicy:
    policy_id: str
    profile: str
    created_at: str
    limits: list[RiskLimitDefinition]
    restricted_symbols: list[str] = field(default_factory=list)
    notes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "limits": [limit.to_dict() for limit in self.limits],
        }


@dataclass(frozen=True)
class RiskControlPolicyManifest:
    policy_id: str
    profile: str
    created_at: str
    policy_path: str
    limit_count: int
    restricted_symbol_count: int
    status: str = "valid"
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskLimitUsageSnapshot:
    usage_id: str
    created_at: str
    trade_date: str
    scope: str
    batch_id: str
    metric: str
    value: float
    threshold: float | str | bool | None
    status: str
    limit_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskControlBreach:
    breach_id: str
    created_at: str
    limit_id: str
    scope: str
    metric: str
    value: float | str | bool | None
    threshold: float | str | bool | None
    severity: str
    action: str
    status: str
    message: str
    order_id: str | None = None
    ts_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskControlDecision:
    decision_id: str
    created_at: str
    order_id: str
    trade_date: str
    ts_code: str
    side: str
    status: str
    action: str
    original_order_value: float
    final_order_value: float
    original_shares: int
    final_shares: int
    breach_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KillSwitchState:
    active: bool
    activated_at: str | None = None
    activated_by: str | None = None
    reason: str = ""
    deactivated_at: str | None = None
    deactivated_by: str | None = None
    deactivation_reason: str = ""
    approval_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskOverrideRequest:
    override_id: str
    created_at: str
    scope: str
    reason: str
    requested_by: str
    expires_at: str | None = None
    max_usage_count: int | None = None
    approval_id: str | None = None
    status: str = "pending_approval"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskOverrideApprovalSummary:
    override_id: str
    approval_id: str
    status: str
    scope: str
    expires_at: str | None = None
    max_usage_count: int | None = None
    applied_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskControlAuditEvent:
    event_id: str
    created_at: str
    event_type: str
    status: str
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskControlReport:
    report_id: str
    created_at: str
    policy_id: str
    profile: str
    trade_date: str
    batch_id: str
    scope: str
    status: str
    accepted_orders: int
    rejected_orders: int
    clipped_orders: int
    warning_count: int
    error_count: int
    blocker_count: int
    breaches: list[RiskControlBreach] = field(default_factory=list)
    decisions: list[RiskControlDecision] = field(default_factory=list)
    usage: list[RiskLimitUsageSnapshot] = field(default_factory=list)
    kill_switch: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "breaches": [breach.to_dict() for breach in self.breaches],
            "decisions": [decision.to_dict() for decision in self.decisions],
            "usage": [item.to_dict() for item in self.usage],
        }
