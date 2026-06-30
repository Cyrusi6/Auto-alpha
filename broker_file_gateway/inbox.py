"""Inbox import and synthetic round-trip files for broker file dry runs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_jsonl_artifact

from .mapping import row_to_internal
from .models import BrokerFileProfile, BrokerFileBatchStatus
from .state import LocalBrokerFileGatewayStore, utc_now


def synthesize_inbox_files(
    *,
    outbox_dir: str | Path,
    inbox_dir: str | Path,
    profile: BrokerFileProfile,
    file_batch_id: str = "",
    reject_every: int = 0,
) -> dict[str, str]:
    outbox = Path(outbox_dir)
    inbox = Path(inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    orders = _read_orders(outbox / "broker_orders.jsonl")
    ack: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    for index, order in enumerate(orders):
        rejected = reject_every > 0 and (index + 1) % reject_every == 0
        ack.append({"client_order_id": order["client_order_id"], "broker_batch_id": order.get("broker_batch_id", ""), "ack_status": "ACK", "trade_date": order.get("trade_date", ""), "ts_code": order.get("ts_code", "")})
        status = "REJECTED" if rejected else "FILLED"
        status_rows.append({"client_order_id": order["client_order_id"], "status": status, "reason": "synthetic_reject" if rejected else "", "trade_date": order.get("trade_date", ""), "ts_code": order.get("ts_code", "")})
        if rejected:
            rejects.append({**order, "status": "REJECTED", "reason": "synthetic_reject"})
        else:
            fills.append({**order, "broker_fill_id": f"bff_{order['client_order_id']}", "status": "FILLED", "value": order.get("order_value", 0.0), "cost": 0.0})
    paths = {
        "broker_ack_path": str(inbox / "broker_ack.jsonl"),
        "broker_status_path": str(inbox / "broker_status.jsonl"),
        "broker_fills_path": str(inbox / "broker_fills.jsonl"),
        "broker_rejects_path": str(inbox / "broker_rejects.jsonl"),
    }
    write_jsonl_artifact(paths["broker_ack_path"], ack, artifact_type="normalized_broker_file_ack", producer="broker_file_gateway")
    write_jsonl_artifact(paths["broker_status_path"], status_rows, artifact_type="normalized_broker_file_status", producer="broker_file_gateway")
    write_jsonl_artifact(paths["broker_fills_path"], fills, artifact_type="normalized_broker_file_fills", producer="broker_file_gateway")
    write_jsonl_artifact(paths["broker_rejects_path"], rejects, artifact_type="normalized_broker_file_rejects", producer="broker_file_gateway")
    return paths


def import_inbox_files(
    *,
    store_dir: str | Path,
    inbox_dir: str | Path,
    output_dir: str | Path,
    profile: BrokerFileProfile,
    file_batch_id: str | None = None,
) -> dict[str, Any]:
    inbox = Path(inbox_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    ack = _read_any(inbox, "broker_ack", profile)
    status = _read_any(inbox, "broker_status", profile)
    fills = _read_any(inbox, "broker_fills", profile)
    rejects = _read_any(inbox, "broker_rejects", profile)
    paths = {
        "normalized_broker_file_ack_path": str(output / "normalized_broker_file_ack.jsonl"),
        "normalized_broker_file_status_path": str(output / "normalized_broker_file_status.jsonl"),
        "normalized_broker_file_fills_path": str(output / "normalized_broker_file_fills.jsonl"),
        "normalized_broker_file_rejects_path": str(output / "normalized_broker_file_rejects.jsonl"),
    }
    write_jsonl_artifact(paths["normalized_broker_file_ack_path"], ack, artifact_type="normalized_broker_file_ack", producer="broker_file_gateway")
    write_jsonl_artifact(paths["normalized_broker_file_status_path"], status, artifact_type="normalized_broker_file_status", producer="broker_file_gateway")
    write_jsonl_artifact(paths["normalized_broker_file_fills_path"], fills, artifact_type="normalized_broker_file_fills", producer="broker_file_gateway")
    write_jsonl_artifact(paths["normalized_broker_file_rejects_path"], rejects, artifact_type="normalized_broker_file_rejects", producer="broker_file_gateway")
    store = LocalBrokerFileGatewayStore(store_dir)
    batch = store.load_batch(file_batch_id)
    if batch:
        status_value = BrokerFileBatchStatus.acknowledged if ack else batch.status
        if fills:
            status_value = BrokerFileBatchStatus.filled
        store.update_batch(batch, status=status_value, imported_at=utc_now(), inbox_paths=paths)
    return {"status": "imported" if (ack or status or fills or rejects) else "waiting_inbox", "file_batch_id": file_batch_id or (batch.file_batch_id if batch else ""), "ack_count": len(ack), "status_count": len(status), "fill_count": len(fills), "reject_count": len(rejects), "paths": paths}


def _read_orders(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_any(root: Path, stem: str, profile: BrokerFileProfile) -> list[dict[str, Any]]:
    jsonl = root / f"{stem}.jsonl"
    csv_path = root / f"{stem}.csv"
    if jsonl.exists():
        return [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    if csv_path.exists():
        with csv_path.open("r", encoding=profile.encoding, newline="") as handle:
            return [row_to_internal(dict(row), profile) for row in csv.DictReader(handle)]
    return []
