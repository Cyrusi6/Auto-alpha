"""Risk override approval helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from approval import ApprovalBatch, ApprovalStatus, LocalApprovalStore
from artifact_schema.writer import write_json_artifact

from .models import RiskOverrideApprovalSummary, RiskOverrideRequest
from .state import LocalRiskControlState


def create_override_approval(
    *,
    approval_store_dir: str | Path,
    output_dir: str | Path,
    state_dir: str | Path,
    scope: str,
    reason: str,
    requested_by: str = "local_user",
    expires_at: str | None = None,
    max_usage_count: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[RiskOverrideRequest, ApprovalBatch, Path]:
    created_at = _utc_now()
    override_id = f"risk_override_{_safe_time(created_at)}"
    approval_id = f"approval_risk_override_{_safe_time(created_at)}"
    request = RiskOverrideRequest(
        override_id=override_id,
        created_at=created_at,
        scope=scope,
        reason=reason,
        requested_by=requested_by,
        expires_at=expires_at,
        max_usage_count=max_usage_count,
        approval_id=approval_id,
        metadata=metadata or {},
    )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    request_path = write_json_artifact(output / "risk_override_request.json", request.to_dict(), artifact_type="risk_override_request", producer="risk_controls")
    batch = ApprovalBatch(
        approval_id=approval_id,
        created_at=created_at,
        factor_id="risk_control_override",
        factor_type="risk_control",
        rebalance_date=str((metadata or {}).get("trade_date") or ""),
        portfolio_method="risk_control_override",
        orders=[],
        approval_type="risk_control_override",
        risk_override_request_path=str(request_path),
        risk_override_scope=scope,
        risk_override_expiry_date=expires_at,
        risk_override_max_usage_count=max_usage_count,
        metadata={"risk_override_request": request.to_dict(), "risk_control_state_dir": str(state_dir), **(metadata or {})},
    )
    LocalApprovalStore(approval_store_dir).save_batch(batch)
    LocalRiskControlState(state_dir).append_audit("risk_override_requested", "pending_approval", reason, {"approval_id": approval_id})
    return request, batch, request_path


def apply_approved_override(
    *,
    approval_store_dir: str | Path,
    approval_id: str,
    state_dir: str | Path,
    actor: str = "local_user",
    deactivate_kill_switch: bool = False,
) -> RiskOverrideApprovalSummary:
    batch = LocalApprovalStore(approval_store_dir).load_batch(approval_id)
    if batch.status != ApprovalStatus.approved:
        raise ValueError(f"risk override approval must be approved before use: {approval_id} is {batch.status}")
    request = dict((batch.metadata or {}).get("risk_override_request") or {})
    summary = RiskOverrideApprovalSummary(
        override_id=str(request.get("override_id") or approval_id),
        approval_id=approval_id,
        status="applied",
        scope=str(request.get("scope") or batch.risk_override_scope or "global"),
        expires_at=request.get("expires_at") or batch.risk_override_expiry_date,
        max_usage_count=request.get("max_usage_count") or batch.risk_override_max_usage_count,
        applied_at=_utc_now(),
        metadata={"actor": actor, "deactivate_kill_switch": deactivate_kill_switch},
    )
    store = LocalRiskControlState(state_dir)
    store.append_override_record(summary)
    store.append_audit("risk_override_applied", "applied", "approved risk override applied", {"approval_id": approval_id, "actor": actor})
    if deactivate_kill_switch:
        store.deactivate_kill_switch("approved risk override", actor=actor, approval_id=approval_id)
    return summary


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_time(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")
