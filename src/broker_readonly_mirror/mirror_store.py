"""Local store for read-only broker mirror snapshots."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import BrokerReadonlySnapshot


class LocalBrokerReadonlyMirrorStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.state_path = self.root_dir / "readonly_mirror_state.json"
        self.snapshots_path = self.root_dir / "readonly_mirror_snapshots.jsonl"
        self.events_path = self.root_dir / "readonly_mirror_events.jsonl"

    def find_snapshot(self, connectivity_session_id: str, as_of_date: str) -> BrokerReadonlySnapshot | None:
        key = f"{connectivity_session_id}:{as_of_date}"
        payload = (_read_json(self.state_path).get("snapshots") or {}).get(key)
        return _snapshot_from_payload(payload) if payload else None

    def save_snapshot(self, snapshot: BrokerReadonlySnapshot, *, refresh: bool = False) -> BrokerReadonlySnapshot:
        existing = self.find_snapshot(snapshot.connectivity_session_id, snapshot.as_of_date)
        if existing and not refresh:
            self.append_event("snapshot_replay", existing.snapshot_id, {"as_of_date": existing.as_of_date})
            return existing
        self.root_dir.mkdir(parents=True, exist_ok=True)
        state = _read_json(self.state_path)
        state.setdefault("snapshots", {})[f"{snapshot.connectivity_session_id}:{snapshot.as_of_date}"] = snapshot.to_dict()
        state["updated_at"] = _utc_now()
        write_json_artifact(self.state_path, state, "readonly_mirror_state", "broker_readonly_mirror")
        snapshots = [item for item in self.load_snapshots() if item.snapshot_id != snapshot.snapshot_id]
        snapshots.append(snapshot)
        write_jsonl_artifact(self.snapshots_path, [item.to_dict() for item in snapshots], "readonly_mirror_snapshots", "broker_readonly_mirror")
        self.append_event("snapshot_saved", snapshot.snapshot_id, {"status": snapshot.status})
        return snapshot

    def load_snapshots(self) -> list[BrokerReadonlySnapshot]:
        return [_snapshot_from_payload(row) for row in _read_jsonl(self.snapshots_path)]

    def append_event(self, event_type: str, snapshot_id: str, metadata: dict[str, Any] | None = None) -> None:
        rows = _read_jsonl(self.events_path)
        rows.append({"event_id": f"readonly_mirror_event_{_utc_id()}_{event_type}", "event_type": event_type, "snapshot_id": snapshot_id, "created_at": _utc_now(), "metadata": metadata or {}})
        write_jsonl_artifact(self.events_path, rows, "readonly_mirror_events", "broker_readonly_mirror")


def _snapshot_from_payload(payload: dict[str, Any]) -> BrokerReadonlySnapshot:
    return BrokerReadonlySnapshot(
        snapshot_id=str(payload.get("snapshot_id") or ""),
        connectivity_session_id=str(payload.get("connectivity_session_id") or ""),
        account_id=str(payload.get("account_id") or ""),
        broker_name=str(payload.get("broker_name") or ""),
        trade_date=str(payload.get("trade_date") or ""),
        as_of_date=str(payload.get("as_of_date") or ""),
        status=str(payload.get("status") or ""),
        cash=dict(payload.get("cash") or {}),
        positions=[dict(item) for item in payload.get("positions", [])],
        orders=[dict(item) for item in payload.get("orders", [])],
        fills=[dict(item) for item in payload.get("fills", [])],
        statements=[dict(item) for item in payload.get("statements", [])],
        source_hash=str(payload.get("source_hash") or ""),
        created_at=str(payload.get("created_at") or ""),
        issues=[dict(item) for item in payload.get("issues", [])],
        metadata=dict(payload.get("metadata") or {}),
    )


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
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _utc_id() -> str:
    return _utc_now().replace("-", "").replace(":", "").replace("Z", "")

