"""Local broker connectivity session store."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import BrokerConnectivitySession


class LocalBrokerConnectivityStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.sessions_path = self.root_dir / "broker_connectivity_sessions.jsonl"
        self.state_path = self.root_dir / "broker_connectivity_state.json"
        self.events_path = self.root_dir / "broker_connectivity_events.jsonl"

    def load_sessions(self) -> list[BrokerConnectivitySession]:
        return [_session_from_payload(row) for row in _read_jsonl(self.sessions_path)]

    def find_session(self, profile_hash: str, trade_date: str, approval_id: str | None) -> BrokerConnectivitySession | None:
        key = _session_key(profile_hash, trade_date, approval_id)
        state = _read_json(self.state_path)
        payload = (state.get("sessions") or {}).get(key)
        return _session_from_payload(payload) if payload else None

    def save_session(self, session: BrokerConnectivitySession, *, refresh: bool = False) -> BrokerConnectivitySession:
        key = _session_key(session.profile_hash, session.trade_date, session.approval_id)
        existing = self.find_session(session.profile_hash, session.trade_date, session.approval_id)
        if existing and not refresh:
            self.append_event("session_replay", existing.session_id, {"profile_name": existing.profile_name})
            return existing
        self.root_dir.mkdir(parents=True, exist_ok=True)
        state = _read_json(self.state_path)
        state.setdefault("sessions", {})[key] = session.to_dict()
        state["updated_at"] = _utc_now()
        write_json_artifact(self.state_path, state, "broker_connectivity_state", "broker_connectivity")
        sessions = [item for item in self.load_sessions() if item.session_id != session.session_id]
        sessions.append(session)
        write_jsonl_artifact(self.sessions_path, [item.to_dict() for item in sessions], "broker_connectivity_sessions", "broker_connectivity")
        self.append_event("session_saved", session.session_id, {"status": session.status, "profile_name": session.profile_name})
        return session

    def append_event(self, event_type: str, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        event = {
            "event_id": f"broker_conn_event_{_utc_id()}_{event_type}",
            "event_type": event_type,
            "session_id": session_id,
            "created_at": _utc_now(),
            "metadata": metadata or {},
        }
        rows = _read_jsonl(self.events_path)
        rows.append(event)
        write_jsonl_artifact(self.events_path, rows, "broker_connectivity_events", "broker_connectivity")


def build_session(profile_hash: str, profile_name: str, broker_name: str, account_id: str, trade_date: str, as_of_date: str, approval_id: str | None, status: str, probe_report_path: str = "") -> BrokerConnectivitySession:
    created = _utc_now()
    return BrokerConnectivitySession(
        session_id=f"broker_conn_session_{_utc_id(created)}_{profile_hash[:8]}",
        profile_hash=profile_hash,
        profile_name=profile_name,
        broker_name=broker_name,
        account_id=account_id,
        trade_date=trade_date,
        as_of_date=as_of_date,
        approval_id=approval_id,
        status=status,
        created_at=created,
        probe_report_path=probe_report_path,
        metadata={"real_submit_supported": False},
    )


def _session_from_payload(payload: dict[str, Any]) -> BrokerConnectivitySession:
    return BrokerConnectivitySession(
        session_id=str(payload.get("session_id") or ""),
        profile_hash=str(payload.get("profile_hash") or ""),
        profile_name=str(payload.get("profile_name") or ""),
        broker_name=str(payload.get("broker_name") or ""),
        account_id=str(payload.get("account_id") or ""),
        trade_date=str(payload.get("trade_date") or ""),
        as_of_date=str(payload.get("as_of_date") or ""),
        approval_id=payload.get("approval_id"),
        status=str(payload.get("status") or ""),
        created_at=str(payload.get("created_at") or ""),
        probe_report_path=str(payload.get("probe_report_path") or ""),
        metadata=dict(payload.get("metadata") or {}),
    )


def _session_key(profile_hash: str, trade_date: str, approval_id: str | None) -> str:
    return f"{profile_hash}:{trade_date}:{approval_id or ''}"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _utc_id(value: str | None = None) -> str:
    return (value or _utc_now()).replace("-", "").replace(":", "").replace("Z", "")

