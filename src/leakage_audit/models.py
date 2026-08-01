"""Dataclasses for future-data leakage audit artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class LeakageSeverity:
    info = "info"
    warning = "warning"
    error = "error"
    blocker = "blocker"


@dataclass(frozen=True)
class LeakageIssue:
    severity: str
    code: str
    message: str
    artifact: str | None = None
    key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaLeakageScanResult:
    scanned_formula_count: int
    blocked_formula_count: int
    warning_formula_count: int
    supported_future_token_count: int
    issues: list[LeakageIssue]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactorValueLeakageResult:
    factor_id: str | None
    checked_records: int
    future_date_count: int
    inactive_security_count: int
    metadata_missing_count: int
    issues: list[LeakageIssue]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TruncationConsistencyResult:
    compared_formula_count: int
    max_abs_diff: float
    changed_value_count: int
    changed_value_ratio: float
    failed_formula_count: int
    skipped_formula_count: int
    passed: bool
    issues: list[LeakageIssue]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BacktestLeakageResult:
    checked: bool
    warning_count: int
    blocker_count: int
    inactive_security_order_count: int
    same_day_signal_execution_warning: bool
    leakage_gate_status: str
    issues: list[LeakageIssue]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SurvivorshipAuditResult:
    current_only_security_master: bool
    warning_count: int
    issues: list[LeakageIssue]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CorporateActionLeakageResult:
    checked_events: int
    corporate_action_future_event_count: int
    unavailable_action_used_count: int
    adjustment_reconciliation_warning_count: int
    total_return_mismatch_count: int
    issues: list[LeakageIssue]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LeakageAuditConfig:
    data_dir: str
    factor_store_dir: str | None
    output_dir: str
    as_of_date: str | None
    cutoff_date: str | None
    point_in_time: bool
    feature_cutoff_mode: str
    strict: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LeakageAuditReport:
    created_at: str
    status: str
    blocker_count: int
    error_count: int
    warning_count: int
    config: LeakageAuditConfig
    formula_scan: FormulaLeakageScanResult
    truncation_consistency: TruncationConsistencyResult
    factor_value_leakage: FactorValueLeakageResult
    backtest_leakage: BacktestLeakageResult
    survivorship: SurvivorshipAuditResult
    corporate_action_leakage: CorporateActionLeakageResult
    issues: list[LeakageIssue]
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "status": self.status,
            "blocker_count": self.blocker_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "config": self.config.to_dict(),
            "formula_scan": self.formula_scan.to_dict(),
            "truncation_consistency": self.truncation_consistency.to_dict(),
            "factor_value_leakage": self.factor_value_leakage.to_dict(),
            "backtest_leakage": self.backtest_leakage.to_dict(),
            "survivorship": self.survivorship.to_dict(),
            "corporate_action_leakage": self.corporate_action_leakage.to_dict(),
            "corporate_action_future_event_count": self.corporate_action_leakage.corporate_action_future_event_count,
            "unavailable_action_used_count": self.corporate_action_leakage.unavailable_action_used_count,
            "adjustment_reconciliation_warning_count": self.corporate_action_leakage.adjustment_reconciliation_warning_count,
            "total_return_mismatch_count": self.corporate_action_leakage.total_return_mismatch_count,
            "issues": [issue.to_dict() for issue in self.issues],
            "paths": self.paths,
            "leakage_gate_status": "blocked" if self.blocker_count else ("warning" if self.warning_count else "passed"),
        }
