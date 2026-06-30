"""Models for local read-only broker connectivity UAT."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class BrokerConnectivityMode:
    offline_mock = "offline_mock"
    local_file_fixture = "local_file_fixture"
    network_readonly_uat = "network_readonly_uat"
    disabled = "disabled"


class BrokerConnectivityStatus:
    skipped = "skipped"
    passed = "passed"
    warning = "warning"
    failed = "failed"
    blocked = "blocked"


class BrokerCapabilityLevel:
    mock_only = "mock_only"
    file_dry_run = "file_dry_run"
    readonly_uat = "readonly_uat"
    manual_pilot_review = "manual_pilot_review"


PROHIBITED_METHODS = [
    "submit_order",
    "submit_orders",
    "cancel_order",
    "replace_order",
    "modify_order",
    "trade",
    "withdraw",
    "transfer",
]


@dataclass(frozen=True)
class BrokerCredentialRef:
    ref_id: str
    name: str
    env_var: str
    required: bool = False
    secret_type: str = "token"
    redaction_hint: str = "redacted"
    present: bool = False
    hash_prefix: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerConnectionProfile:
    profile_id: str
    profile_name: str
    broker_name: str
    connectivity_mode: str
    endpoint_kind: str
    base_url_env_var: str = ""
    account_id_env_var: str = ""
    credential_refs: list[BrokerCredentialRef] = field(default_factory=list)
    timeout_seconds: float = 5.0
    allowed_methods: list[str] = field(default_factory=list)
    readonly_methods: list[str] = field(default_factory=list)
    prohibited_methods: list[str] = field(default_factory=lambda: list(PROHIBITED_METHODS))
    schema_mapping: dict[str, Any] = field(default_factory=dict)
    notice: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "credential_refs": [ref.to_dict() for ref in self.credential_refs],
        }


@dataclass(frozen=True)
class BrokerNetworkGuard:
    allow_network: bool
    env_gate_name: str = "BROKER_UAT_ALLOW_NETWORK"
    env_gate_value: str = ""
    approval_required: bool = False
    approval_id: str | None = None
    approved: bool = False
    readonly_only: bool = True
    blocked_reason: str = ""
    status: str = BrokerConnectivityStatus.skipped

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerConnectivityIssue:
    severity: str
    code: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerConnectivityProbeResult:
    probe_id: str
    profile_id: str
    profile_name: str
    broker_name: str
    connectivity_mode: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    account_id: str
    trade_date: str
    as_of_date: str
    ping: dict[str, Any] = field(default_factory=dict)
    server_time: dict[str, Any] = field(default_factory=dict)
    account_snapshot: dict[str, Any] = field(default_factory=dict)
    record_counts: dict[str, int] = field(default_factory=dict)
    network_guard: dict[str, Any] = field(default_factory=dict)
    credential_summary: dict[str, Any] = field(default_factory=dict)
    readonly_enforcement: dict[str, Any] = field(default_factory=dict)
    issues: list[BrokerConnectivityIssue] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class BrokerConnectivitySession:
    session_id: str
    profile_hash: str
    profile_name: str
    broker_name: str
    account_id: str
    trade_date: str
    as_of_date: str
    approval_id: str | None
    status: str
    created_at: str
    probe_report_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerConnectivityReport:
    report_id: str
    created_at: str
    status: str
    profile: dict[str, Any]
    credential_refs: list[dict[str, Any]]
    network_guard: dict[str, Any]
    probe_result: dict[str, Any]
    session: dict[str, Any]
    summary: dict[str, Any]
    issues: list[dict[str, Any]] = field(default_factory=list)
    real_submit_supported: bool = False
    legal_notice: str = "Read-only UAT connectivity evidence only; no broker authorization, no order submission, and no trading permission."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BrokerConnectivityBlockedError(RuntimeError):
    """Raised when a guarded connectivity operation is not allowed."""

