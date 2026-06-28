"""Shadow book persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def find_order_records(orders_dir: str | Path, execution_plan_dir: str | Path | None = None) -> tuple[list[dict[str, Any]], str]:
    candidates = []
    if execution_plan_dir:
        root = Path(execution_plan_dir)
        candidates.extend([root / "child_orders.jsonl", root / "parent_orders.jsonl"])
    root = Path(orders_dir)
    candidates.extend([root / "plan" / "child_orders.jsonl", root / "child_orders.jsonl", root / "orders.jsonl"])
    for path in candidates:
        rows = read_jsonl(path)
        if rows:
            return rows, str(path)
    return [], ""
