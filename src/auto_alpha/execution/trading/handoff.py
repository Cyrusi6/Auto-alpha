"""Operator handoff evidence, checklist, storage, reporting, and command workflow."""

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

_CHECKLIST: tuple[tuple[str, str], ...] = (
    ("data_freeze_validated", "Data freeze has been validated."),
    ("active_model_confirmed", "Active production factor/model has been confirmed."),
    ("active_optimizer_policy_confirmed", "Active optimizer policy has been confirmed."),
    ("factor_certification_checked", "Factor certification evidence has been checked."),
    ("portfolio_certification_checked", "Portfolio policy certification has been checked."),
    ("risk_gate_passed", "Pre-trade risk gate has passed."),
    ("kill_switch_inactive", "Kill switch is inactive."),
    ("order_approval_approved", "Order approval is approved."),
    ("broker_file_manifest_reviewed", "Broker file manifest has been reviewed."),
    ("checksum_verified", "Outbox file checksums have been verified."),
    ("outbox_record_count_checked", "Outbox record count has been checked."),
    ("order_notional_checked", "Order notional has been checked."),
    ("restricted_symbol_absent", "Restricted symbols are absent."),
    ("operator_readme_reviewed", "Operator readme has been reviewed."),
    ("handoff_directory_confirmed", "Handoff directory has been confirmed."),
    ("no_real_auto_submit_confirmed", "No real auto submit path is enabled."),
    ("inbox_expected_files_documented", "Expected inbox files are documented."),
    ("rollback_contact_or_runbook_reviewed", "Rollback contact/runbook has been reviewed."),
    ("second_reviewer_confirmed", "Second reviewer has confirmed the package."),
)


def default_handoff_checklist() -> list[HandoffChecklistItem]:
    return [HandoffChecklistItem(item_id=item_id, title=title, description=title) for item_id, title in _CHECKLIST]


def required_item_ids() -> list[str]:
    return [item_id for item_id, _title in _CHECKLIST]

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



class LocalOperatorHandoffStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.packages_dir = self.root_dir / "handoffs"
        self.state_path = self.root_dir / "operator_handoff_state.json"
        self.events_path = self.root_dir / "operator_handoff_events.jsonl"
        self.evidence_path = self.root_dir / "operator_handoff_evidence.jsonl"

    def save_package(self, package: OperatorHandoffPackage) -> Path:
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        path = self.package_path(package.handoff_id)
        write_json_artifact(path, package.to_dict(), artifact_type="operator_handoff_package", producer="operator_handoff")
        self._update_state(package)
        self.append_event("save", package.handoff_id, package.status, {"file_batch_id": package.file_batch_id})
        return path

    def load_package(self, handoff_id: str) -> OperatorHandoffPackage:
        path = self.package_path(handoff_id)
        if not path.exists():
            raise FileNotFoundError(f"operator handoff package not found: {handoff_id}")
        return package_from_payload(json.loads(path.read_text(encoding="utf-8")))

    def load_by_file_batch(self, file_batch_id: str) -> OperatorHandoffPackage | None:
        for package in self.list_packages():
            if package.file_batch_id == file_batch_id:
                return package
        return None

    def list_packages(self, status: str | None = None) -> list[OperatorHandoffPackage]:
        if not self.packages_dir.exists():
            return []
        packages = [package_from_payload(json.loads(path.read_text(encoding="utf-8"))) for path in sorted(self.packages_dir.glob("*.json"))]
        if status is not None:
            packages = [package for package in packages if package.status == status]
        return packages

    def mark_item(
        self,
        handoff_id: str,
        item_id: str,
        *,
        checked: bool = True,
        status: str | None = None,
        checked_by: str = "local_operator",
        evidence_path: str | None = None,
        note: str | None = None,
    ) -> OperatorHandoffPackage:
        package = self.load_package(handoff_id)
        updated_items: list[HandoffChecklistItem] = []
        found = False
        for item in package.checklist:
            if item.item_id == item_id:
                found = True
                item_status = status or ("checked" if checked else "pending")
                item_checked = bool(checked and item_status == "checked")
                updated_items.append(
                    replace(
                        item,
                        status=item_status,
                        checked=item_checked,
                        checked_by=checked_by if item_status in {"checked", "failed", "skipped"} else None,
                        checked_at=_handoff_store_utc_now() if item_status in {"checked", "failed", "skipped"} else None,
                        evidence_path=evidence_path,
                        evidence_refs=[evidence_path] if evidence_path else list(item.evidence_refs),
                        note=note,
                    )
                )
            else:
                updated_items.append(item)
        if not found:
            raise ValueError(f"unknown handoff checklist item: {item_id}")
        package_status = HandoffStatus.reviewed if all(item.checked or not item.required for item in updated_items) else package.status
        updated = replace(package, checklist=updated_items, status=package_status)
        self.save_package(updated)
        self.append_event("mark_item", handoff_id, package_status, {"item_id": item_id, "checked": checked, "item_status": status})
        return updated

    def add_evidence(self, record: HandoffEvidenceRecord) -> OperatorHandoffPackage:
        package = self.load_package(record.handoff_id)
        updated = replace(package, evidence=[*package.evidence, record])
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self.evidence_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        self.save_package(updated)
        self.append_event("add_evidence", record.handoff_id, updated.status, {"evidence_id": record.evidence_id})
        return updated

    def update_status(self, handoff_id: str, status: str, metadata: dict[str, Any] | None = None) -> OperatorHandoffPackage:
        package = self.load_package(handoff_id)
        updated = replace(package, status=status, metadata={**package.metadata, **(metadata or {})})
        self.save_package(updated)
        self.append_event("status", handoff_id, status, metadata or {})
        return updated

    def package_path(self, handoff_id: str) -> Path:
        return self.packages_dir / f"{handoff_id}.json"

    def append_event(self, event: str, handoff_id: str, status: str, metadata: dict[str, Any] | None = None) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        payload = {"event": event, "handoff_id": handoff_id, "status": status, "created_at": _handoff_store_utc_now(), "metadata": metadata or {}}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def _update_state(self, package: OperatorHandoffPackage) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        state = {"packages": {}}
        if self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                state = {"packages": {}}
        packages = dict(state.get("packages") or {})
        packages[package.handoff_id] = {
            "handoff_id": package.handoff_id,
            "file_batch_id": package.file_batch_id,
            "status": package.status,
            "updated_at": _handoff_store_utc_now(),
        }
        write_json_artifact(
            self.state_path,
            {"packages": packages},
            artifact_type="operator_handoff_state",
            producer="operator_handoff",
        )


def create_package(
    *,
    handoff_id: str,
    file_batch_id: str,
    approval_id: str,
    production_run_id: str,
    trade_date: str,
    broker_file_gateway_report_path: str,
    broker_file_manifest_path: str,
    checksum_manifest_path: str,
    outbox_dir: str,
    handoff_dir: str,
    mapping_certification_decision_path: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> OperatorHandoffPackage:
    return OperatorHandoffPackage(
        handoff_id=handoff_id,
        created_at=_handoff_store_utc_now(),
        status=HandoffStatus.prepared,
        file_batch_id=file_batch_id,
        approval_id=approval_id,
        production_run_id=production_run_id,
        trade_date=trade_date,
        broker_file_gateway_report_path=broker_file_gateway_report_path,
        broker_file_manifest_path=broker_file_manifest_path,
        checksum_manifest_path=checksum_manifest_path,
        outbox_dir=outbox_dir,
        handoff_dir=handoff_dir,
        mapping_certification_decision_path=mapping_certification_decision_path,
        checklist=default_handoff_checklist(),
        metadata=metadata or {},
    )


def package_from_payload(payload: dict[str, Any]) -> OperatorHandoffPackage:
    checklist = [_checklist_item_from_payload(item) for item in payload.get("checklist", [])]
    evidence = [_evidence_from_payload(item) for item in payload.get("evidence", [])]
    return OperatorHandoffPackage(
        handoff_id=str(payload["handoff_id"]),
        created_at=str(payload.get("created_at") or ""),
        status=str(payload.get("status") or HandoffStatus.planned),
        file_batch_id=str(payload.get("file_batch_id") or ""),
        approval_id=str(payload.get("approval_id") or ""),
        production_run_id=str(payload.get("production_run_id") or ""),
        trade_date=str(payload.get("trade_date") or ""),
        broker_file_gateway_report_path=str(payload.get("broker_file_gateway_report_path") or ""),
        broker_file_manifest_path=str(payload.get("broker_file_manifest_path") or ""),
        checksum_manifest_path=str(payload.get("checksum_manifest_path") or ""),
        outbox_dir=str(payload.get("outbox_dir") or ""),
        handoff_dir=str(payload.get("handoff_dir") or ""),
        mapping_certification_decision_path=payload.get("mapping_certification_decision_path"),
        checklist=checklist,
        evidence=evidence,
        approval_status=payload.get("approval_status"),
        reviewer=payload.get("reviewer"),
        local_approval_id=payload.get("local_approval_id"),
        metadata=dict(payload.get("metadata") or {}),
    )


def _checklist_item_from_payload(payload: dict[str, Any]) -> HandoffChecklistItem:
    return HandoffChecklistItem(
        item_id=str(payload["item_id"]),
        title=str(payload.get("title") or payload["item_id"]),
        description=str(payload.get("description") or payload.get("title") or ""),
        required=bool(payload.get("required", True)),
        status=str(payload.get("status") or ("checked" if payload.get("checked") else "pending")),
        checked=bool(payload.get("checked", False)),
        checked_by=payload.get("checked_by"),
        checked_at=payload.get("checked_at"),
        evidence_path=payload.get("evidence_path"),
        evidence_refs=list(payload.get("evidence_refs") or ([payload["evidence_path"]] if payload.get("evidence_path") else [])),
        note=payload.get("note"),
    )


def _evidence_from_payload(payload: dict[str, Any]) -> HandoffEvidenceRecord:
    return HandoffEvidenceRecord(
        evidence_id=str(payload["evidence_id"]),
        handoff_id=str(payload["handoff_id"]),
        evidence_type=str(payload.get("evidence_type") or "evidence"),
        path=str(payload.get("path") or ""),
        description=str(payload.get("description") or ""),
        created_at=str(payload.get("created_at") or ""),
        sha256=payload.get("sha256"),
        size_bytes=payload.get("size_bytes"),
        recorded_by=payload.get("recorded_by"),
        metadata=dict(payload.get("metadata") or {}),
    )


def write_events_sidecar(store: LocalOperatorHandoffStore) -> None:
    if store.events_path.exists():
        rows = [json.loads(line) for line in store.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        write_jsonl_artifact(store.events_path, rows, artifact_type="operator_handoff_events", producer="operator_handoff")


def _handoff_store_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any



def add_evidence_record(
    store_dir: str | Path,
    handoff_id: str,
    evidence_type: str,
    path: str | Path,
    description: str = "",
    recorded_by: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> HandoffEvidenceRecord:
    target = Path(path)
    sha256: str | None = None
    size_bytes: int | None = None
    if target.exists() and target.is_file():
        data = target.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        size_bytes = len(data)
    record = HandoffEvidenceRecord(
        evidence_id=f"evidence_{handoff_id}_{_safe_name(evidence_type)}_{_safe_time()}",
        handoff_id=handoff_id,
        evidence_type=evidence_type,
        path=str(target),
        description=description,
        created_at=_handoff_evidence_utc_now(),
        sha256=sha256,
        size_bytes=size_bytes,
        recorded_by=recorded_by,
        metadata={**(metadata or {}), "exists": target.exists()},
    )
    LocalOperatorHandoffStore(store_dir).add_evidence(record)
    return record


def _handoff_evidence_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_time() -> str:
    return _handoff_evidence_utc_now().replace("-", "").replace(":", "").replace("Z", "")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_") or "item"

from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def build_operator_handoff_report(package: OperatorHandoffPackage) -> OperatorHandoffReport:
    required = [item for item in package.checklist if item.required]
    checked = [item for item in required if item.checked]
    missing = [item.item_id for item in required if not item.checked]
    return OperatorHandoffReport(
        handoff_id=package.handoff_id,
        status=package.status,
        required_items=len(required),
        checked_required_items=len(checked),
        missing_required_items=missing,
        evidence_count=len(package.evidence),
        approval_status=package.approval_status,
        no_real_submit_confirmed=any(item.item_id == "no_real_auto_submit_confirmed" and item.checked for item in package.checklist),
        metadata=package.metadata,
    )


def write_operator_handoff_report(store_dir: str | Path, handoff_id: str, output_dir: str | Path | None = None) -> dict[str, Any]:
    store = LocalOperatorHandoffStore(store_dir)
    package = store.load_package(handoff_id)
    report = build_operator_handoff_report(package)
    target = Path(output_dir) if output_dir is not None else Path(store_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "operator_handoff_report.json"
    md_path = target / "operator_handoff_report.md"
    events_path = target / "operator_handoff_events.jsonl"
    checklist_path = target / "operator_handoff_checklist.jsonl"
    evidence_path = target / "operator_handoff_evidence.jsonl"
    payload = {
        **report.to_dict(),
        "handoff_package_path": str(store.package_path(package.handoff_id)),
        "file_batch_id": package.file_batch_id,
        "approval_id": package.approval_id,
        "broker_file_gateway_report_path": package.broker_file_gateway_report_path,
        "broker_file_manifest_path": package.broker_file_manifest_path,
        "checksum_manifest_path": package.checksum_manifest_path,
    }
    write_json_artifact(json_path, payload, artifact_type="operator_handoff_report", producer="operator_handoff")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    write_jsonl_artifact(
        checklist_path,
        [item.to_dict() for item in package.checklist],
        artifact_type="operator_handoff_checklist",
        producer="operator_handoff",
    )
    write_jsonl_artifact(
        evidence_path,
        [record.to_dict() for record in package.evidence],
        artifact_type="operator_handoff_evidence",
        producer="operator_handoff",
    )
    if store.events_path.exists():
        rows = [__import__("json").loads(line) for line in store.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        write_jsonl_artifact(events_path, rows, artifact_type="operator_handoff_events", producer="operator_handoff")
    return {
        "status": report.status,
        "handoff_id": package.handoff_id,
        "report_path": str(json_path),
        "report_md_path": str(md_path),
        "events_path": str(events_path) if events_path.exists() else "",
        "checklist_path": str(checklist_path),
        "evidence_path": str(evidence_path),
        "missing_required_items": list(report.missing_required_items),
        "checked_required_items": report.checked_required_items,
        "required_items": report.required_items,
    }


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Operator Handoff Report",
        "",
        f"- handoff_id: `{payload.get('handoff_id')}`",
        f"- status: `{payload.get('status')}`",
        f"- file_batch_id: `{payload.get('file_batch_id')}`",
        f"- required items: {payload.get('checked_required_items')}/{payload.get('required_items')}",
        f"- approval_status: `{payload.get('approval_status')}`",
        f"- no_real_submit_confirmed: `{payload.get('no_real_submit_confirmed')}`",
        "",
        "## Missing Required Items",
    ]
    missing = payload.get("missing_required_items") or []
    lines.extend([f"- `{item}`" for item in missing] or ["- none"])
    return "\n".join(lines) + "\n"

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from auto_alpha.platform.governance.approval import ApprovalBatch, ApprovalOrder, ApprovalStatus, ApprovalType, LocalApprovalStore



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and manage operator handoff packages.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["create", "mark-item", "add-evidence", "create-approval", "apply-approved", "show", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--handoff-store-dir", required=True)
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--handoff-id")
    parser.add_argument("--file-batch-id", default="file_batch_smoke")
    parser.add_argument("--approval-id", default="approval_smoke")
    parser.add_argument("--production-run-id", default="production_smoke")
    parser.add_argument("--trade-date", default="20240104")
    parser.add_argument("--broker-file-gateway-report-path", default="")
    parser.add_argument("--broker-file-manifest-path", default="")
    parser.add_argument("--checksum-manifest-path", default="")
    parser.add_argument("--outbox-dir", default="")
    parser.add_argument("--handoff-dir", default="")
    parser.add_argument("--mapping-certification-decision-path")
    parser.add_argument("--item-id")
    parser.add_argument("--status", choices=["checked", "failed", "skipped"], default="checked")
    parser.add_argument("--operator")
    parser.add_argument("--checked-by", default="local_operator")
    parser.add_argument("--evidence-path")
    parser.add_argument("--evidence-type", default="review_note")
    parser.add_argument("--description", default="")
    parser.add_argument("--reviewer", default="local_reviewer")
    parser.add_argument("--second-reviewer")
    parser.add_argument("--comment", default="approved_for_file_outbox_dry_run")
    parser.add_argument("--auto-check-all", action="store_true")
    parser.add_argument("--auto-confirm-local-smoke", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = LocalOperatorHandoffStore(args.handoff_store_dir)
    output_dir = Path(args.output_dir or args.handoff_store_dir)
    handoff_id = args.handoff_id or f"handoff_{args.file_batch_id}"
    checked_by = args.operator or args.checked_by
    if args.command in {"show", "report"} and not args.handoff_id:
        resolved = _resolve_handoff(args.handoff_store_dir)
        if resolved is not None:
            args.handoff_store_dir, handoff_id = str(resolved[0]), resolved[1]
            store = LocalOperatorHandoffStore(args.handoff_store_dir)
    if args.command in {"create", "smoke"}:
        existing = store.load_by_file_batch(args.file_batch_id)
        package = existing or create_package(
            handoff_id=handoff_id,
            file_batch_id=args.file_batch_id,
            approval_id=args.approval_id,
            production_run_id=args.production_run_id,
            trade_date=args.trade_date,
            broker_file_gateway_report_path=args.broker_file_gateway_report_path,
            broker_file_manifest_path=args.broker_file_manifest_path,
            checksum_manifest_path=args.checksum_manifest_path,
            outbox_dir=args.outbox_dir,
            handoff_dir=args.handoff_dir or str(output_dir),
            mapping_certification_decision_path=args.mapping_certification_decision_path,
            metadata={"no_real_submit": True, "mode": "file_outbox_dry_run", "second_reviewer": args.second_reviewer},
        )
        store.save_package(package)
        if args.command == "smoke" or args.auto_check_all or args.auto_confirm_local_smoke:
            for item_id in required_item_ids():
                package = store.mark_item(package.handoff_id, item_id, checked=True, status="checked", checked_by=checked_by)
        if args.command == "smoke":
            evidence_path = output_dir / "smoke_evidence.txt"
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text("local smoke evidence for broker file handoff dry-run\n", encoding="utf-8")
            add_evidence_record(
                args.handoff_store_dir,
                package.handoff_id,
                "smoke",
                evidence_path,
                "local smoke evidence",
                recorded_by=checked_by,
                metadata={"local_smoke_auto_confirm": True},
            )
            if args.approval_store_dir:
                _create_local_approval(args.approval_store_dir, package, reviewer=args.reviewer, comment=args.comment)
                package = replace(store.load_package(package.handoff_id), approval_status=ApprovalStatus.approved, local_approval_id=f"handoff_{package.handoff_id}")
                store.save_package(package)
            payload = write_operator_handoff_report(args.handoff_store_dir, package.handoff_id, output_dir)
        else:
            payload = {"status": "success", "handoff_id": package.handoff_id, "handoff_package_path": str(store.package_path(package.handoff_id))}
    elif args.command == "mark-item":
        if not args.item_id:
            raise SystemExit("--item-id is required")
        package = store.mark_item(
            handoff_id,
            args.item_id,
            checked=args.status == "checked",
            status=args.status,
            checked_by=checked_by,
            evidence_path=args.evidence_path,
        )
        payload = {"status": "success", "handoff_id": package.handoff_id, "package_status": package.status}
    elif args.command == "add-evidence":
        if not args.evidence_path:
            raise SystemExit("--evidence-path is required")
        record = add_evidence_record(args.handoff_store_dir, handoff_id, args.evidence_type, args.evidence_path, args.description, recorded_by=checked_by)
        payload = {"status": "success", "evidence": record.to_dict()}
    elif args.command == "create-approval":
        package = store.load_package(handoff_id)
        approval = _create_local_approval(args.approval_store_dir or args.handoff_store_dir, package, reviewer="", comment="")
        payload = {"status": "success", "approval_id": approval.approval_id, "approval_status": approval.status}
    elif args.command == "apply-approved":
        package = store.load_package(handoff_id)
        approval_store = LocalApprovalStore(args.approval_store_dir or args.handoff_store_dir)
        approval_id = package.local_approval_id or f"handoff_{package.handoff_id}"
        approval = approval_store.load_batch(approval_id)
        if approval.status != ApprovalStatus.approved:
            raise SystemExit(f"handoff approval is not approved: {approval_id} is {approval.status}")
        package = replace(package, status=HandoffStatus.approved, approval_status=approval.status, local_approval_id=approval_id)
        store.save_package(package)
        payload = {"status": "success", "handoff_id": package.handoff_id, "approval_id": approval_id}
    elif args.command == "show":
        package = store.load_package(handoff_id)
        payload = {"status": "found", "package": package.to_dict()}
    elif args.command == "report":
        payload = write_operator_handoff_report(args.handoff_store_dir, handoff_id, output_dir)
    else:  # pragma: no cover
        payload = {"status": "failed", "error": f"unsupported command: {args.command}"}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 1 if payload.get("status") == "failed" else 0


def _create_local_approval(store_dir: str | Path, package: Any, reviewer: str = "", comment: str = "") -> ApprovalBatch:
    approval_id = f"handoff_{package.handoff_id}"
    approval = ApprovalBatch(
        approval_id=approval_id,
        created_at=package.created_at,
        factor_id=package.file_batch_id,
        factor_type="broker_file_handoff",
        rebalance_date=package.trade_date,
        portfolio_method="file_outbox_dry_run",
        orders=[ApprovalOrder(trade_date=package.trade_date, ts_code="HANDOFF", side="REVIEW", target_weight=0.0, order_value=0.0, reason="operator_handoff")],
        approval_type=ApprovalType.broker_file_handoff,
        broker_file_batch_id=package.file_batch_id,
        operator_handoff_id=package.handoff_id,
        broker_file_gateway_report_path=package.broker_file_gateway_report_path,
        operator_handoff_report_path="",
        broker_file_summary={"outbox_dir": package.outbox_dir, "no_real_submit": True},
        operator_handoff_summary={"handoff_id": package.handoff_id},
        status=ApprovalStatus.pending,
        metadata={"mode": "file_outbox_dry_run", "no_real_submit": True},
    )
    store = LocalApprovalStore(store_dir)
    store.save_batch(approval)
    if reviewer:
        approval = store.approve(approval.approval_id, reviewer=reviewer, comment=comment)
    return approval


def _resolve_handoff(root_dir: str | Path) -> tuple[Path, str] | None:
    root = Path(root_dir)
    direct = LocalOperatorHandoffStore(root).list_packages()
    if direct:
        return root, sorted(direct, key=lambda package: package.created_at)[-1].handoff_id
    package_paths = sorted(root.rglob("handoffs/*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not package_paths:
        return None
    package_path = package_paths[0]
    return package_path.parent.parent, package_path.stem


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "HandoffChecklistItem",
    "HandoffEvidenceRecord",
    "HandoffStatus",
    "OperatorHandoffPackage",
    "OperatorHandoffReport",
    "LocalOperatorHandoffStore",
    "add_evidence_record",
    "default_handoff_checklist",
    "required_item_ids",
    "write_operator_handoff_report",
]
