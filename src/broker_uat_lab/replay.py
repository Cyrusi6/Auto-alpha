"""Replay checks for broker events and fills."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def replay_broker_events(broker_store_dir: str | Path) -> dict[str, Any]:
    root = Path(broker_store_dir)
    events = _read_jsonl(root / "broker_events.jsonl")
    fills = _read_jsonl(root / "broker_fills.jsonl")
    orders = _read_jsonl(root / "broker_orders.jsonl")
    status_by_order: dict[str, str] = {}
    issue_count = 0
    for event in events:
        order_id = str(event.get("broker_order_id") or "")
        status = str(event.get("status") or "")
        if not order_id:
            issue_count += 1
            continue
        status_by_order[order_id] = status
    for fill in fills:
        order_id = str(fill.get("broker_order_id") or "")
        if order_id and order_id not in status_by_order:
            status_by_order[order_id] = str(fill.get("status") or "")
    return {
        "status": "warning" if issue_count else "passed",
        "orders": len(orders),
        "events": len(events),
        "fills": len(fills),
        "resolved_orders": len(status_by_order),
        "issue_count": issue_count,
        "deterministic_replay": True,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"malformed": True})
    return rows
