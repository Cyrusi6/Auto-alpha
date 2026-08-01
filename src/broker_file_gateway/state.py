"""Local state store for broker file dry-run batches."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .models import BrokerFileBatch, BrokerFileBatchStatus


class LocalBrokerFileGatewayStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.batches_path = self.root_dir / "broker_file_batches.jsonl"
        self.state_path = self.root_dir / "broker_file_batch_state.json"
        self.events_path = self.root_dir / "broker_file_events.jsonl"
        self.issues_path = self.root_dir / "broker_file_roundtrip_issues.jsonl"

    def save_batch(self, batch: BrokerFileBatch) -> BrokerFileBatch:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        state = self._load_state()
        state.setdefault("batches", {})[batch.file_batch_id] = batch.to_dict()
        state.setdefault("idempotency", {})[_idempotency_key(batch)] = batch.file_batch_id
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        self._rewrite_batches()
        write_json_artifact(self.root_dir / "broker_file_batch.json", batch.to_dict(), artifact_type="broker_file_batch", producer="broker_file_gateway")
        self.append_event("save_batch", batch.file_batch_id, batch.status, {"order_count": batch.order_count})
        return batch

    def update_batch(self, batch: BrokerFileBatch, status: str | None = None, **changes: Any) -> BrokerFileBatch:
        payload = batch.to_dict()
        payload.update(changes)
        if status is not None:
            payload["status"] = status
        updated = BrokerFileBatch(**payload)
        return self.save_batch(updated)

    def find_existing(self, production_run_id: str, approval_id: str, profile_id: str) -> BrokerFileBatch | None:
        state = self._load_state()
        batch_id = state.get("idempotency", {}).get(f"{production_run_id}|{approval_id}|{profile_id}")
        return self.load_batch(batch_id) if batch_id else None

    def load_batch(self, file_batch_id: str | None = None) -> BrokerFileBatch | None:
        state = self._load_state().get("batches", {})
        if file_batch_id is None:
            if not state:
                return None
            payload = list(state.values())[-1]
        else:
            payload = state.get(file_batch_id)
        return BrokerFileBatch(**payload) if payload else None

    def list_batches(self) -> list[BrokerFileBatch]:
        return [BrokerFileBatch(**payload) for payload in self._load_state().get("batches", {}).values()]

    def append_event(self, event_type: str, file_batch_id: str, status: str, metadata: dict[str, Any] | None = None) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "event_type": event_type,
            "file_batch_id": file_batch_id,
            "status": status,
            "created_at": utc_now(),
            "metadata": metadata or {},
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def _rewrite_batches(self) -> None:
        records = list(self._load_state().get("batches", {}).values())
        with self.batches_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                handle.write("\n")

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"batches": {}, "idempotency": {}}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"batches": {}, "idempotency": {}}


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _idempotency_key(batch: BrokerFileBatch) -> str:
    return f"{batch.production_run_id}|{batch.approval_id}|{batch.profile_id}"
