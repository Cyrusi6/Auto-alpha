"""Local JSON store for operator handoff packages."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .checklist import default_handoff_checklist
from .models import HandoffChecklistItem, HandoffEvidenceRecord, HandoffStatus, OperatorHandoffPackage


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
                        checked_at=_utc_now() if item_status in {"checked", "failed", "skipped"} else None,
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
        payload = {"event": event, "handoff_id": handoff_id, "status": status, "created_at": _utc_now(), "metadata": metadata or {}}
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
            "updated_at": _utc_now(),
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
        created_at=_utc_now(),
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


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
