"""Models for local BrokerAdapter UAT."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class BrokerUatStatus:
    passed = "passed"
    warning = "warning"
    failed = "failed"
    skipped = "skipped"


class BrokerUatScenarioType:
    submit_idempotency = "submit_idempotency"
    full_fill = "full_fill"
    partial_fill = "partial_fill"
    reject_order = "reject_order"
    cancel_order = "cancel_order"
    replace_order = "replace_order"
    duplicate_fill = "duplicate_fill"
    out_of_order_fill = "out_of_order_fill"
    missing_ack = "missing_ack"
    reconnect_replay = "reconnect_replay"
    rate_limit = "rate_limit"
    kill_switch_block = "kill_switch_block"
    file_outbox_roundtrip = "file_outbox_roundtrip"
    eod_reconciliation = "eod_reconciliation"
    settlement_reconciliation = "settlement_reconciliation"


@dataclass(frozen=True)
class BrokerUatScenario:
    scenario_id: str
    scenario_type: str
    title: str
    expected_status: str = BrokerUatStatus.passed
    expected_outcome: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerUatResult:
    scenario_id: str
    scenario_type: str
    status: str
    message: str
    observed: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerAdapterCapabilityManifest:
    adapter_name: str
    supports_submit: bool
    supports_cancel: bool
    supports_replace: bool
    supports_status_poll: bool
    supports_fills: bool
    supports_file_outbox: bool
    supports_statement_import: bool
    supports_idempotency: bool
    supports_kill_switch_block: bool
    real_network_required: bool = False
    real_broker_credentials_required: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerAdapterContractReport:
    adapter_name: str
    status: str
    scenario_count: int
    passed_count: int
    failed_count: int
    warning_count: int
    skipped_count: int
    capability_manifest: dict[str, Any]
    results: list[dict[str, Any]]
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerUatPlan:
    plan_id: str
    profile: str
    adapter: str
    scenarios: list[BrokerUatScenario]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "profile": self.profile,
            "adapter": self.adapter,
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
        }


@dataclass(frozen=True)
class BrokerUatReport:
    uat_id: str
    created_at: str
    status: str
    plan: dict[str, Any]
    contract_report: dict[str, Any]
    replay_report: dict[str, Any]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
