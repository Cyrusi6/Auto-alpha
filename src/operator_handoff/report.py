"""Operator handoff report writer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import OperatorHandoffPackage, OperatorHandoffReport
from .store import LocalOperatorHandoffStore


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
