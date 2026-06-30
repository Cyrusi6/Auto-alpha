"""Dataclasses for shadow lab analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ShadowLabConfig:
    output_dir: str
    start_date: str | None = None
    end_date: str | None = None
    drift_threshold: float = 0.05
    min_shadow_days: int = 1
    replay_report_path: str | None = None
    replay_dir: str | None = None
    shadow_root_dir: str | None = None
    production_root_dir: str | None = None
    paper_account_dir: str | None = None
    settlement_dir: str | None = None
    portfolio_lab_report_path: str | None = None
    portfolio_certification_decision_path: str | None = None
    certified_portfolio_policy_path: str | None = None
    backtest_result_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowDaySummary:
    trade_date: str
    production_run_id: str | None
    status: str
    shadow_fill_rate: float
    order_count: int
    fill_count: int
    rejected_count: int
    target_weight_drift: float
    position_weight_drift: float
    equity: float
    daily_return: float
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowLabIssue:
    severity: str
    code: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowLabReport:
    status: str
    created_at: str
    config: dict[str, Any]
    day_summaries: list[ShadowDaySummary]
    performance_summary: dict[str, Any]
    drift_summary: dict[str, Any]
    calibration_suggestions: list[dict[str, Any]]
    issues: list[ShadowLabIssue]
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "created_at": self.created_at,
            "config": dict(self.config),
            "day_summaries": [item.to_dict() for item in self.day_summaries],
            "performance_summary": dict(self.performance_summary),
            "drift_summary": dict(self.drift_summary),
            "calibration_suggestions": list(self.calibration_suggestions),
            "issues": [item.to_dict() for item in self.issues],
            "paths": dict(self.paths),
        }
