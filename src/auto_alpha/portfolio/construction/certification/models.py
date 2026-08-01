"""Dataclasses for portfolio policy certification."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class PortfolioCertificationStatus:
    certified = "certified"
    conditional = "conditional"
    rejected = "rejected"
    needs_review = "needs_review"
    insufficient_data = "insufficient_data"


@dataclass(frozen=True)
class PortfolioCertificationPolicy:
    policy_id: str
    profile_name: str
    require_portfolio_lab: bool = True
    require_factor_certification: bool = False
    min_selection_score: float = -999.0
    min_scenario_pass_ratio: float = 0.0
    min_successful_trial_count: int = 1
    min_fill_rate: float = 0.0
    max_constraint_reject_rate: float = 1.0
    max_avg_turnover: float = 1.0
    max_tracking_error: float = 1.0
    max_capacity_warning_count: int = 999
    max_risk_constraint_violations: float = 999.0
    allow_conditional: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioCertificationCheck:
    name: str
    status: str
    severity: str
    value: float | int | str | bool | None = None
    threshold: float | int | str | bool | None = None
    reason: str = ""
    artifact_refs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioCertificationScorecard:
    portfolio_policy_id: str
    factor_id: str
    policy_id: str
    policy_profile: str
    checks: list[PortfolioCertificationCheck]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checks"] = [check.to_dict() for check in self.checks]
        return payload


@dataclass(frozen=True)
class PortfolioCertificationDecision:
    portfolio_policy_id: str
    factor_id: str
    status: str
    passed: bool
    reasons: list[str]
    required_remediation: list[str]
    checks: dict[str, Any]
    policy_id: str
    policy_profile: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioCertificationPackage:
    portfolio_policy_id: str
    factor_id: str
    portfolio_policy: dict[str, Any]
    certification_policy: dict[str, Any]
    scorecard: dict[str, Any]
    decision: dict[str, Any]
    source_artifacts: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
