"""Local state store for multi-day replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import ProductionReplayDayResult, ProductionReplayEvent


class LocalProductionReplayStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.day_state_path = self.root_dir / "production_replay_state.json"
        self.days_path = self.root_dir / "production_replay_days.jsonl"
        self.events_path = self.root_dir / "production_replay_events.jsonl"
        self.approvals_path = self.root_dir / "production_replay_approvals.jsonl"

    def load_days(self, replay_id: str | None = None) -> list[dict[str, Any]]:
        rows = _read_jsonl(self.days_path)
        if replay_id:
            rows = [row for row in rows if row.get("replay_id") == replay_id]
        return rows

    def load_day(self, replay_id: str, trade_date: str) -> dict[str, Any] | None:
        for row in reversed(self.load_days(replay_id)):
            if row.get("trade_date") == trade_date:
                return row
        return None

    def save_day(self, result: ProductionReplayDayResult) -> None:
        rows = [
            row
            for row in self.load_days()
            if not (row.get("replay_id") == result.replay_id and row.get("trade_date") == result.trade_date)
        ]
        rows.append(result.to_dict())
        write_jsonl_artifact(self.days_path, rows, "production_replay_days", "production_replay")
        self._write_state(result.replay_id)

    def append_event(self, event: ProductionReplayEvent) -> None:
        rows = self.load_events()
        rows.append(event.to_dict())
        write_jsonl_artifact(self.events_path, rows, "production_replay_events", "production_replay")

    def load_events(self, replay_id: str | None = None) -> list[dict[str, Any]]:
        rows = _read_jsonl(self.events_path)
        if replay_id:
            rows = [row for row in rows if row.get("replay_id") == replay_id]
        return rows

    def record_approval(self, replay_id: str, trade_date: str, approval_id: str, mode: str) -> None:
        rows = [
            row
            for row in _read_jsonl(self.approvals_path)
            if not (row.get("replay_id") == replay_id and row.get("trade_date") == trade_date and row.get("approval_id") == approval_id)
        ]
        rows.append({"replay_id": replay_id, "trade_date": trade_date, "approval_id": approval_id, "approval_mode": mode})
        write_jsonl_artifact(self.approvals_path, rows, "production_replay_approvals", "production_replay")

    def _write_state(self, replay_id: str) -> None:
        days = self.load_days(replay_id)
        payload = {
            "replay_id": replay_id,
            "day_count": len(days),
            "completed_trade_dates": [row.get("trade_date") for row in days if row.get("status") in {"success", "warning"}],
            "failed_trade_dates": [row.get("trade_date") for row in days if row.get("status") == "failed"],
            "blocked_trade_dates": [row.get("trade_date") for row in days if row.get("status") == "blocked"],
        }
        write_json_artifact(self.day_state_path, payload, "production_replay_state", "production_replay")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows
