"""Dataclasses for local program trading compliance evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ComplianceEvidenceStatus:
    complete = "complete"
    warning = "warning"
    missing = "missing"
    not_applicable = "not_applicable"
    failed = "failed"


class ComplianceEvidenceCategory:
    account = "account"
    strategy = "strategy"
    model = "model"
    portfolio_policy = "portfolio_policy"
    risk_control = "risk_control"
    execution = "execution"
    broker_file = "broker_file"
    data = "data"
    system = "system"
    software = "software"
    operation = "operation"
    incident = "incident"
    monitoring = "monitoring"
    approval = "approval"
    readiness = "readiness"


@dataclass(frozen=True)
class ProgramTradingSystemInventory:
    inventory_id: str
    created_at: str
    software_name: str
    software_version: str
    git_commit: str
    package_version: str
    python_version: str
    platform: str
    module_inventory_path: str | None = None
    cli_inventory_path: str | None = None
    dependency_inventory_path: str | None = None
    dashboard_import_status: str = "not_checked"
    network_default_disabled: bool = True
    real_broker_submit_supported: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProgramTradingStrategyInventory:
    active_model_version_id: str | None = None
    active_optimizer_policy_model_version_id: str | None = None
    factor_id: str | None = None
    portfolio_policy_id: str | None = None
    data_freeze_id: str | None = None
    factor_certification_status: str | None = None
    portfolio_certification_status: str | None = None
    validation_status: str | None = None
    alpha_campaign_id: str | None = None
    risk_policy_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProgramTradingRiskControlInventory:
    pre_trade_risk_controls_enabled: bool = False
    kill_switch_available: bool = False
    risk_override_approval_required: bool = False
    max_order_value: float | None = None
    max_participation: float | None = None
    settlement_aware: bool = False
    eod_reconciliation_enabled: bool = False
    incident_response_enabled: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProgramTradingEvidenceRecord:
    evidence_id: str
    category: str
    title: str
    status: str
    source_path: str | None = None
    sha256: str | None = None
    size_bytes: int = 0
    summary: str = ""
    owner: str = "local_platform"
    reviewer: str | None = None
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProgramTradingComplianceChecklist:
    item_id: str
    title: str
    status: str
    required: bool = True
    reason: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    reviewer: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SecretScanFinding:
    path: str
    line: int
    severity: str
    code: str
    message: str
    excerpt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SecretScanReport:
    created_at: str
    scanned_files: int
    finding_count: int
    blocker_count: int
    warning_count: int
    findings: list[SecretScanFinding]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class ComplianceGapReport:
    created_at: str
    gap_count: int
    missing_required_count: int
    warning_count: int
    gaps: list[dict[str, Any]]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProgramTradingCompliancePack:
    compliance_pack_id: str
    created_at: str
    status: str
    system_inventory: dict[str, Any]
    strategy_inventory: dict[str, Any]
    risk_control_inventory: dict[str, Any]
    evidence_records: list[dict[str, Any]]
    checklist: list[dict[str, Any]]
    gap_report: dict[str, Any]
    secret_scan_report: dict[str, Any]
    summary: dict[str, Any]
    real_broker_submit_supported: bool = False
    legal_notice: str = "Local evidence organization only; not legal advice, regulatory filing, broker authorization, or trading permission."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComplianceReviewPackage:
    review_id: str
    created_at: str
    compliance_pack_path: str
    status: str
    reviewer: str | None
    comment: str | None
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
