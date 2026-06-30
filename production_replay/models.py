"""Dataclasses for multi-day production replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ReplayMode:
    shadow_only = "shadow_only"
    paper_simulated = "paper_simulated"
    file_outbox_dry_run = "file_outbox_dry_run"
    mixed = "mixed"


class ReplayDayStatus:
    pending = "pending"
    running = "running"
    planned = "planned"
    success = "success"
    warning = "warning"
    blocked = "blocked"
    failed = "failed"
    skipped = "skipped"
    resumed = "resumed"


class ReplayApprovalMode:
    manual = "manual"
    auto_approve_shadow = "auto_approve_shadow"
    auto_approve_paper_local = "auto_approve_paper_local"
    no_approval = "no_approval"


@dataclass(frozen=True)
class ProductionReplayConfig:
    replay_id: str
    replay_name: str
    replay_mode: str
    start_date: str
    end_date: str
    trade_dates: list[str]
    data_dir: str
    output_dir: str
    replay_state_dir: str
    factor_store_dir: str | None = None
    model_registry_dir: str | None = None
    approval_store_dir: str | None = None
    paper_account_dir: str | None = None
    orders_root_dir: str | None = None
    shadow_root_dir: str | None = None
    settlement_dir: str | None = None
    broker_store_dir: str | None = None
    broker_adapter: str = "paper"
    broker_file_gateway: bool = False
    broker_file_profile: str = "generic_broker_csv"
    broker_file_profile_config: str | None = None
    broker_file_gateway_store_dir: str | None = None
    broker_file_outbox_root_dir: str | None = None
    broker_file_inbox_root_dir: str | None = None
    broker_file_handoff_root_dir: str | None = None
    operator_handoff_store_dir: str | None = None
    operator_handoff_approval_store_dir: str | None = None
    mapping_certification_decision_path: str | None = None
    require_mapping_certification: bool = False
    file_outbox_dry_run: bool = False
    auto_confirm_local_smoke: bool = False
    monitoring_root_dir: str | None = None
    incident_store_dir: str | None = None
    risk_control_state_dir: str | None = None
    risk_control_output_root: str | None = None
    portfolio_value: float = 1_000_000.0
    index_code: str = "000300.SH"
    top_n: int = 20
    max_weight: float = 0.10
    capacity_aware: bool = False
    point_in_time: bool = False
    feature_cutoff_mode: str = "same_day_after_close"
    corporate_action_aware: bool = False
    apply_corporate_actions: bool = False
    corporate_action_dir: str | None = None
    target_return_mode: str = "adjusted_close"
    settlement_aware: bool = False
    risk_controls: bool = False
    data_freeze_dir: str | None = None
    data_version_manifest_path: str | None = None
    require_data_freeze: bool = False
    require_active_model: bool = False
    require_active_optimizer_policy: bool = False
    certified_portfolio_policy_path: str | None = None
    portfolio_certification_decision_path: str | None = None
    require_certified_portfolio_policy: bool = False
    auto_approve_paper_local: bool = False
    paper_local_reviewer: str = "local_replay_reviewer"
    stop_on_blocker: bool = False
    continue_on_warning: bool = True
    max_failed_days: int = 0
    strict_calendar: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionReplayPlan:
    replay_id: str
    replay_name: str
    replay_mode: str
    created_at: str
    start_date: str
    end_date: str
    trade_dates: list[str]
    day_count: int
    config: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionReplayDayResult:
    replay_id: str
    trade_date: str
    run_mode: str
    status: str
    production_run_id: str | None = None
    approval_id: str | None = None
    plan_status: str = ""
    run_status: str = ""
    resume_status: str = ""
    close_status: str = ""
    gate_blocker_count: int = 0
    gate_warning_count: int = 0
    phase_failed_count: int = 0
    phase_blocked_count: int = 0
    shadow_fill_rate: float = 0.0
    paper_fill_rate: float = 0.0
    broker_unfilled_value: float = 0.0
    incident_open_count: int = 0
    paths: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionReplayEvent:
    replay_id: str
    trade_date: str | None
    event_type: str
    status: str
    created_at: str
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionReplayReport:
    replay_id: str
    replay_name: str
    replay_mode: str
    status: str
    created_at: str
    start_date: str
    end_date: str
    day_results: list[ProductionReplayDayResult]
    summary: dict[str, Any]
    paths: dict[str, str] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "replay_id": self.replay_id,
            "replay_name": self.replay_name,
            "replay_mode": self.replay_mode,
            "status": self.status,
            "created_at": self.created_at,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "day_results": [item.to_dict() for item in self.day_results],
            "summary": dict(self.summary),
            "paths": dict(self.paths),
            "config": dict(self.config),
        }
