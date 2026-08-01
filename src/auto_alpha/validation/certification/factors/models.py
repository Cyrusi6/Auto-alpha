"""Dataclasses for factor certification."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class CertificationStatus:
    certified = "certified"
    conditional = "conditional"
    rejected = "rejected"
    needs_review = "needs_review"
    insufficient_data = "insufficient_data"


class CertificationSeverity:
    info = "info"
    warning = "warning"
    error = "error"
    blocker = "blocker"


@dataclass(frozen=True)
class CertificationPolicy:
    policy_id: str
    profile_name: str
    require_data_freeze: bool = False
    require_pit_passed: bool = False
    require_leakage_passed: bool = False
    require_alpha_lineage: bool = False
    require_validation_lab: bool = True
    require_multiple_testing: bool = True
    require_overfit_risk: bool = True
    require_placebo: bool = True
    require_stress_backtest: bool = True
    max_pbo: float = 0.75
    min_deflated_ic_score: float = -999.0
    min_out_of_sample_score: float = -999.0
    min_window_pass_ratio: float = 0.0
    min_placebo_percentile: float = 0.0
    max_null_exceedance_ratio: float = 1.0
    max_train_test_decay: float = 999.0
    max_turnover: float = 1.0
    max_capacity_warning_count: int = 999
    max_risk_control_blocker_count: int = 0
    max_settlement_reconciliation_error_count: int = 0
    max_eod_break_count: int = 0
    max_validation_blocker_count: int = 0
    min_regime_pass_ratio: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactorCertificationCheck:
    name: str
    status: str
    severity: str
    value: float | str | bool | None = None
    threshold: float | str | bool | None = None
    reason: str = ""
    artifact_refs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactorCertificationScorecard:
    factor_id: str
    policy_id: str
    policy_profile: str
    checks: list[FactorCertificationCheck]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checks"] = [check.to_dict() for check in self.checks]
        return payload


@dataclass(frozen=True)
class FactorCertificationDecision:
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
class FactorCertificationPackage:
    factor_id: str
    policy: dict[str, Any]
    scorecard: dict[str, Any]
    decision: dict[str, Any]
    source_artifacts: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
