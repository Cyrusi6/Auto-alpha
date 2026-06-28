"""Local state and audit store for risk controls."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import KillSwitchState, RiskControlAuditEvent, RiskLimitUsageSnapshot, RiskOverrideApprovalSummary


class LocalRiskControlState:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.state_path = self.root_dir / "risk_control_state.json"
        self.usage_path = self.root_dir / "risk_limit_usage.jsonl"
        self.audit_path = self.root_dir / "risk_control_audit_log.jsonl"
        self.kill_switch_path = self.root_dir / "kill_switch_state.json"
        self.override_records_path = self.root_dir / "risk_override_records.jsonl"

    def load_kill_switch(self) -> KillSwitchState:
        payload = _read_json(self.kill_switch_path)
        if not payload:
            return KillSwitchState(active=False)
        return KillSwitchState(
            active=bool(payload.get("active", False)),
            activated_at=payload.get("activated_at"),
            activated_by=payload.get("activated_by"),
            reason=str(payload.get("reason") or ""),
            deactivated_at=payload.get("deactivated_at"),
            deactivated_by=payload.get("deactivated_by"),
            deactivation_reason=str(payload.get("deactivation_reason") or ""),
            approval_id=payload.get("approval_id"),
            metadata=dict(payload.get("metadata") or {}),
        )

    def save_kill_switch(self, state: KillSwitchState) -> Path:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        return write_json_artifact(self.kill_switch_path, state.to_dict(), artifact_type="kill_switch_state", producer="risk_controls")

    def activate_kill_switch(self, reason: str, actor: str = "local_user") -> KillSwitchState:
        state = KillSwitchState(active=True, activated_at=_utc_now(), activated_by=actor, reason=reason)
        self.save_kill_switch(state)
        self.append_audit("kill_switch_activated", "active", reason, {"actor": actor})
        return state

    def deactivate_kill_switch(self, reason: str, actor: str = "local_user", approval_id: str | None = None) -> KillSwitchState:
        prior = self.load_kill_switch()
        state = KillSwitchState(
            active=False,
            activated_at=prior.activated_at,
            activated_by=prior.activated_by,
            reason=prior.reason,
            deactivated_at=_utc_now(),
            deactivated_by=actor,
            deactivation_reason=reason,
            approval_id=approval_id,
            metadata=prior.metadata,
        )
        self.save_kill_switch(state)
        self.append_audit("kill_switch_deactivated", "inactive", reason, {"actor": actor, "approval_id": approval_id})
        return state

    def append_usage(self, records: list[RiskLimitUsageSnapshot]) -> None:
        if not records:
            return
        self.root_dir.mkdir(parents=True, exist_ok=True)
        existing = {str(row.get("usage_id") or "") for row in _read_jsonl(self.usage_path)}
        new_records = [record for record in records if record.usage_id not in existing]
        if new_records:
            with self.usage_path.open("a", encoding="utf-8") as handle:
                for record in new_records:
                    handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
                    handle.write("\n")
            write_jsonl_artifact(self.usage_path, _read_jsonl(self.usage_path), artifact_type="risk_limit_usage", producer="risk_controls")

    def load_usage(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.usage_path)

    def append_audit(self, event_type: str, status: str, message: str = "", metadata: dict[str, Any] | None = None) -> RiskControlAuditEvent:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        event = RiskControlAuditEvent(
            event_id=f"rce_{_safe_time(_utc_now())}_{len(_read_jsonl(self.audit_path)) + 1}",
            created_at=_utc_now(),
            event_type=event_type,
            status=status,
            message=message,
            metadata=metadata or {},
        )
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return event

    def append_override_record(self, record: RiskOverrideApprovalSummary) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self.override_records_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        write_jsonl_artifact(self.override_records_path, _read_jsonl(self.override_records_path), artifact_type="risk_override_records", producer="risk_controls")

    def write_state_summary(self, payload: dict[str, Any] | None = None) -> Path:
        summary = {
            "created_at": _utc_now(),
            "kill_switch": self.load_kill_switch().to_dict(),
            "usage_records": len(_read_jsonl(self.usage_path)),
            "audit_events": len(_read_jsonl(self.audit_path)),
            "override_records": len(_read_jsonl(self.override_records_path)),
            **(payload or {}),
        }
        return write_json_artifact(self.state_path, summary, artifact_type="risk_control_state", producer="risk_controls")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_time(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")
