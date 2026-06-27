"""Approval records for local production order batches."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ApprovalStatus:
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"


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
    status: str = ApprovalStatus.pending
    decision: ApprovalDecision | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
