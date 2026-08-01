"""Dataclasses for manual broker file handoff."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class HandoffStatus:
    planned = "planned"
    prepared = "prepared"
    reviewed = "reviewed"
    approved = "approved"
    handed_off = "handed_off"
    inbox_received = "inbox_received"
    completed = "completed"
    rejected = "rejected"
    cancelled = "cancelled"


@dataclass(frozen=True)
class HandoffChecklistItem:
    item_id: str
    title: str
    description: str = ""
    required: bool = True
    status: str = "pending"
    checked: bool = False
    checked_by: str | None = None
    checked_at: str | None = None
    evidence_path: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HandoffEvidenceRecord:
    evidence_id: str
    handoff_id: str
    evidence_type: str
    path: str
    description: str = ""
    created_at: str = ""
    sha256: str | None = None
    size_bytes: int | None = None
    recorded_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OperatorHandoffPackage:
    handoff_id: str
    created_at: str
    status: str
    file_batch_id: str
    approval_id: str
    production_run_id: str
    trade_date: str
    broker_file_gateway_report_path: str
    broker_file_manifest_path: str
    checksum_manifest_path: str
    outbox_dir: str
    handoff_dir: str
    mapping_certification_decision_path: str | None = None
    checklist: list[HandoffChecklistItem] = field(default_factory=list)
    evidence: list[HandoffEvidenceRecord] = field(default_factory=list)
    approval_status: str | None = None
    reviewer: str | None = None
    local_approval_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OperatorHandoffReport:
    handoff_id: str
    status: str
    required_items: int
    checked_required_items: int
    missing_required_items: list[str]
    evidence_count: int
    approval_status: str | None = None
    no_real_submit_confirmed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
