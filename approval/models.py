"""Approval records for local production order batches."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ApprovalStatus:
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"


class ApprovalType:
    order_batch = "order_batch"
    model_lifecycle = "model_lifecycle"
    portfolio_policy_activation = "portfolio_policy_activation"
    account_reconciliation_adjustment = "account_reconciliation_adjustment"
    risk_control_override = "risk_control_override"
    broker_file_handoff = "broker_file_handoff"
    broker_mapping_certification_ack = "broker_mapping_certification_ack"
    compliance_review = "compliance_review"
    broker_uat_review = "broker_uat_review"
    go_live_review = "go_live_review"
    broker_connectivity_review = "broker_connectivity_review"


@dataclass(frozen=True)
class ApprovalOrder:
    trade_date: str
    ts_code: str
    side: str
    target_weight: float
    order_value: float
    reason: str = "rebalance"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ApprovalDecision:
    status: str
    reviewer: str
    decided_at: str
    comment: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ApprovalBatch:
    approval_id: str
    created_at: str
    factor_id: str
    factor_type: str
    rebalance_date: str
    portfolio_method: str
    orders: list[ApprovalOrder]
    risk_summary: dict[str, Any] = field(default_factory=dict)
    parent_orders: list[dict[str, Any]] = field(default_factory=list)
    child_orders: list[dict[str, Any]] = field(default_factory=list)
    capacity_summary: dict[str, Any] = field(default_factory=dict)
    approval_type: str = ApprovalType.order_batch
    model_version_id: str | None = None
    model_lifecycle_action: str | None = None
    model_review_package_path: str | None = None
    lifecycle_summary: dict[str, Any] = field(default_factory=dict)
    reconciliation_report_path: str | None = None
    adjustment_proposals_path: str | None = None
    adjustment_summary: dict[str, Any] = field(default_factory=dict)
    eod_reconciliation_status: str | None = None
    unresolved_break_count: int = 0
    material_break_count: int = 0
    risk_control_report_path: str | None = None
    risk_control_breaches_path: str | None = None
    risk_override_request_path: str | None = None
    risk_control_summary: dict[str, Any] = field(default_factory=dict)
    kill_switch_action: str | None = None
    risk_override_scope: str | None = None
    risk_override_expiry_date: str | None = None
    risk_override_max_usage_count: int | None = None
    broker_file_batch_id: str | None = None
    operator_handoff_id: str | None = None
    broker_mapping_certification_decision_path: str | None = None
    broker_file_gateway_report_path: str | None = None
    operator_handoff_report_path: str | None = None
    broker_file_summary: dict[str, Any] = field(default_factory=dict)
    operator_handoff_summary: dict[str, Any] = field(default_factory=dict)
    compliance_pack_path: str | None = None
    broker_uat_report_path: str | None = None
    broker_connectivity_profile_path: str | None = None
    broker_connectivity_report_path: str | None = None
    broker_readonly_mirror_report_path: str | None = None
    go_live_gate_decision_path: str | None = None
    go_live_status: str | None = None
    compliance_summary: dict[str, Any] = field(default_factory=dict)
    broker_uat_summary: dict[str, Any] = field(default_factory=dict)
    broker_connectivity_summary: dict[str, Any] = field(default_factory=dict)
    broker_readonly_summary: dict[str, Any] = field(default_factory=dict)
    go_live_summary: dict[str, Any] = field(default_factory=dict)
    status: str = ApprovalStatus.pending
    decision: ApprovalDecision | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
