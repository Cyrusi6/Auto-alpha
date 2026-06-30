"""Local JSON approval store."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .models import ApprovalBatch, ApprovalDecision, ApprovalOrder, ApprovalStatus


class LocalApprovalStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.approvals_dir = self.root_dir / "approvals"
        self.log_path = self.root_dir / "approval_log.jsonl"

    def save_batch(self, batch: ApprovalBatch) -> Path:
        self.approvals_dir.mkdir(parents=True, exist_ok=True)
        path = self._batch_path(batch.approval_id)
        write_json_artifact(path, batch.to_dict(), artifact_type="approval_batch", producer="approval")
        self._append_log("save", batch.approval_id, batch.status, {"factor_id": batch.factor_id})
        return path

    def load_batch(self, approval_id: str) -> ApprovalBatch:
        path = self._batch_path(approval_id)
        if not path.exists():
            raise FileNotFoundError(f"approval batch not found: {approval_id}")
        return _batch_from_payload(json.loads(path.read_text(encoding="utf-8")))

    def list_batches(self, status: str | None = None) -> list[ApprovalBatch]:
        if not self.approvals_dir.exists():
            return []
        batches = [_batch_from_payload(json.loads(path.read_text(encoding="utf-8"))) for path in sorted(self.approvals_dir.glob("*.json"))]
        if status is not None:
            batches = [batch for batch in batches if batch.status == status]
        return batches

    def approve(self, approval_id: str, reviewer: str, comment: str | None = None) -> ApprovalBatch:
        batch = self.load_batch(approval_id)
        if batch.status != ApprovalStatus.pending:
            raise ValueError(f"only pending approval batches can be approved: {approval_id} is {batch.status}")
        decision = ApprovalDecision(
            status=ApprovalStatus.approved,
            reviewer=reviewer,
            decided_at=_utc_now(),
            comment=comment,
        )
        updated = _replace_batch_status(batch, ApprovalStatus.approved, decision)
        self._write_batch(updated)
        self._append_log("approve", approval_id, updated.status, {"reviewer": reviewer, "comment": comment})
        return updated

    def reject(self, approval_id: str, reviewer: str, reason: str) -> ApprovalBatch:
        batch = self.load_batch(approval_id)
        if batch.status != ApprovalStatus.pending:
            raise ValueError(f"only pending approval batches can be rejected: {approval_id} is {batch.status}")
        decision = ApprovalDecision(
            status=ApprovalStatus.rejected,
            reviewer=reviewer,
            decided_at=_utc_now(),
            reason=reason,
        )
        updated = _replace_batch_status(batch, ApprovalStatus.rejected, decision)
        self._write_batch(updated)
        self._append_log("reject", approval_id, updated.status, {"reviewer": reviewer, "reason": reason})
        return updated

    def expire_pending(self, as_of_time: str | None = None) -> list[ApprovalBatch]:
        expired: list[ApprovalBatch] = []
        decision_time = as_of_time or _utc_now()
        for batch in self.list_batches(status=ApprovalStatus.pending):
            decision = ApprovalDecision(
                status=ApprovalStatus.expired,
                reviewer="system",
                decided_at=decision_time,
                reason="expired",
            )
            updated = _replace_batch_status(batch, ApprovalStatus.expired, decision)
            self._write_batch(updated)
            self._append_log("expire", updated.approval_id, updated.status, {"as_of_time": decision_time})
            expired.append(updated)
        return expired

    def _write_batch(self, batch: ApprovalBatch) -> None:
        self.approvals_dir.mkdir(parents=True, exist_ok=True)
        write_json_artifact(self._batch_path(batch.approval_id), batch.to_dict(), artifact_type="approval_batch", producer="approval")

    def _batch_path(self, approval_id: str) -> Path:
        return self.approvals_dir / f"{approval_id}.json"

    def _append_log(self, event: str, approval_id: str, status: str, metadata: dict[str, Any] | None = None) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "event": event,
            "approval_id": approval_id,
            "status": status,
            "created_at": _utc_now(),
            "metadata": metadata or {},
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _batch_from_payload(payload: dict[str, Any]) -> ApprovalBatch:
    orders = [ApprovalOrder(**order) for order in payload.get("orders", [])]
    decision_payload = payload.get("decision")
    decision = ApprovalDecision(**decision_payload) if isinstance(decision_payload, dict) else None
    return ApprovalBatch(
        approval_id=str(payload["approval_id"]),
        created_at=str(payload["created_at"]),
        factor_id=str(payload["factor_id"]),
        factor_type=str(payload.get("factor_type") or "unknown"),
        rebalance_date=str(payload["rebalance_date"]),
        portfolio_method=str(payload.get("portfolio_method") or "equal_weight"),
        orders=orders,
        risk_summary=dict(payload.get("risk_summary") or {}),
        parent_orders=[dict(item) for item in payload.get("parent_orders", [])],
        child_orders=[dict(item) for item in payload.get("child_orders", [])],
        capacity_summary=dict(payload.get("capacity_summary") or {}),
        approval_type=str(payload.get("approval_type") or "order_batch"),
        model_version_id=payload.get("model_version_id"),
        model_lifecycle_action=payload.get("model_lifecycle_action"),
        model_review_package_path=payload.get("model_review_package_path"),
        lifecycle_summary=dict(payload.get("lifecycle_summary") or {}),
        reconciliation_report_path=payload.get("reconciliation_report_path"),
        adjustment_proposals_path=payload.get("adjustment_proposals_path"),
        adjustment_summary=dict(payload.get("adjustment_summary") or {}),
        eod_reconciliation_status=payload.get("eod_reconciliation_status"),
        unresolved_break_count=int(payload.get("unresolved_break_count", 0) or 0),
        material_break_count=int(payload.get("material_break_count", 0) or 0),
        risk_control_report_path=payload.get("risk_control_report_path"),
        risk_control_breaches_path=payload.get("risk_control_breaches_path"),
        risk_override_request_path=payload.get("risk_override_request_path"),
        risk_control_summary=dict(payload.get("risk_control_summary") or {}),
        kill_switch_action=payload.get("kill_switch_action"),
        risk_override_scope=payload.get("risk_override_scope"),
        risk_override_expiry_date=payload.get("risk_override_expiry_date"),
        risk_override_max_usage_count=payload.get("risk_override_max_usage_count"),
        broker_file_batch_id=payload.get("broker_file_batch_id"),
        operator_handoff_id=payload.get("operator_handoff_id"),
        broker_mapping_certification_decision_path=payload.get("broker_mapping_certification_decision_path"),
        broker_file_gateway_report_path=payload.get("broker_file_gateway_report_path"),
        operator_handoff_report_path=payload.get("operator_handoff_report_path"),
        broker_file_summary=dict(payload.get("broker_file_summary") or {}),
        operator_handoff_summary=dict(payload.get("operator_handoff_summary") or {}),
        compliance_pack_path=payload.get("compliance_pack_path"),
        broker_uat_report_path=payload.get("broker_uat_report_path"),
        go_live_gate_decision_path=payload.get("go_live_gate_decision_path"),
        go_live_status=payload.get("go_live_status"),
        compliance_summary=dict(payload.get("compliance_summary") or {}),
        broker_uat_summary=dict(payload.get("broker_uat_summary") or {}),
        go_live_summary=dict(payload.get("go_live_summary") or {}),
        status=str(payload.get("status") or ApprovalStatus.pending),
        decision=decision,
        metadata=dict(payload.get("metadata") or {}),
    )


def _replace_batch_status(batch: ApprovalBatch, status: str, decision: ApprovalDecision) -> ApprovalBatch:
    return ApprovalBatch(
        approval_id=batch.approval_id,
        created_at=batch.created_at,
        factor_id=batch.factor_id,
        factor_type=batch.factor_type,
        rebalance_date=batch.rebalance_date,
        portfolio_method=batch.portfolio_method,
        orders=batch.orders,
        risk_summary=batch.risk_summary,
        parent_orders=batch.parent_orders,
        child_orders=batch.child_orders,
        capacity_summary=batch.capacity_summary,
        approval_type=batch.approval_type,
        model_version_id=batch.model_version_id,
        model_lifecycle_action=batch.model_lifecycle_action,
        model_review_package_path=batch.model_review_package_path,
        lifecycle_summary=batch.lifecycle_summary,
        reconciliation_report_path=batch.reconciliation_report_path,
        adjustment_proposals_path=batch.adjustment_proposals_path,
        adjustment_summary=batch.adjustment_summary,
        eod_reconciliation_status=batch.eod_reconciliation_status,
        unresolved_break_count=batch.unresolved_break_count,
        material_break_count=batch.material_break_count,
        risk_control_report_path=batch.risk_control_report_path,
        risk_control_breaches_path=batch.risk_control_breaches_path,
        risk_override_request_path=batch.risk_override_request_path,
        risk_control_summary=batch.risk_control_summary,
        kill_switch_action=batch.kill_switch_action,
        risk_override_scope=batch.risk_override_scope,
        risk_override_expiry_date=batch.risk_override_expiry_date,
        risk_override_max_usage_count=batch.risk_override_max_usage_count,
        broker_file_batch_id=batch.broker_file_batch_id,
        operator_handoff_id=batch.operator_handoff_id,
        broker_mapping_certification_decision_path=batch.broker_mapping_certification_decision_path,
        broker_file_gateway_report_path=batch.broker_file_gateway_report_path,
        operator_handoff_report_path=batch.operator_handoff_report_path,
        broker_file_summary=batch.broker_file_summary,
        operator_handoff_summary=batch.operator_handoff_summary,
        compliance_pack_path=batch.compliance_pack_path,
        broker_uat_report_path=batch.broker_uat_report_path,
        go_live_gate_decision_path=batch.go_live_gate_decision_path,
        go_live_status=batch.go_live_status,
        compliance_summary=batch.compliance_summary,
        broker_uat_summary=batch.broker_uat_summary,
        go_live_summary=batch.go_live_summary,
        status=status,
        decision=decision,
        metadata=batch.metadata,
    )


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
