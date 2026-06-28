"""Dataclasses for portfolio lab artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PortfolioPolicyScenario:
    scenario_id: str
    name: str
    cost_multiplier: float = 1.0
    max_participation: float = 0.10
    max_turnover: float = 1.0
    max_tracking_error: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioPolicyTrial:
    trial_id: str
    policy_id: str
    scenario_id: str
    factor_id: str
    output_dir: str
    status: str
    error: str | None = None
    policy: dict[str, Any] = field(default_factory=dict)
    scenario: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioTrialMetrics:
    trial_id: str
    policy_id: str
    scenario_id: str
    status: str
    score: float
    total_return: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    avg_turnover: float = 0.0
    tracking_error: float = 0.0
    fill_rate: float = 0.0
    constraint_reject_rate: float = 0.0
    capacity_warning_count: float = 0.0
    risk_constraint_violations: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioLabIssue:
    severity: str
    code: str
    message: str
    trial_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioLabConfig:
    data_dir: str
    factor_store_dir: str
    output_dir: str
    factor_id: str | None = None
    factor_type: str = "composite"
    latest_approved: bool = True
    index_code: str = "000300.SH"
    scenario_profile: str = "sample"
    max_trials: int | None = None
    pretty: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioLabResult:
    lab_id: str
    created_at: str
    status: str
    factor_id: str
    config: dict[str, Any]
    trials: list[dict[str, Any]]
    metrics: list[dict[str, Any]]
    robustness: dict[str, Any]
    selected_policy: dict[str, Any] | None
    issues: list[dict[str, Any]]
    paths: dict[str, str]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
