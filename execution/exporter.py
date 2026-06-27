"""Export paper orders and fills."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Sequence

from .models import ExecutionFill, ExecutionOrder


def _record_payload(record: object) -> dict[str, object]:
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)
    if isinstance(record, dict):
        return dict(record)
    raise TypeError(f"unsupported export record: {type(record)!r}")


def export_orders_csv(orders: Sequence[ExecutionOrder], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payloads = [_record_payload(order) for order in orders]
    fieldnames = sorted({key for payload in payloads for key in payload}) if payloads else []
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(payloads)
    return output_path


def export_orders_jsonl(orders: Sequence[ExecutionOrder], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for order in orders:
            handle.write(json.dumps(_record_payload(order), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return output_path


def export_fills_jsonl(fills: Sequence[ExecutionFill], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for fill in fills:
            handle.write(json.dumps(_record_payload(fill), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return output_path
