"""Report writer for broker file gateway artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import BrokerFileGatewayReport, BrokerFileProfile
from .state import LocalBrokerFileGatewayStore


def write_gateway_report(
    *,
    store_dir: str | Path,
    output_dir: str | Path | None = None,
    profile: BrokerFileProfile | None = None,
    file_batch_id: str | None = None,
    manifest: dict[str, Any] | None = None,
    roundtrip: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = LocalBrokerFileGatewayStore(store_dir)
    batch = store.load_batch(file_batch_id)
    root = Path(output_dir) if output_dir is not None else Path(store_dir)
    root.mkdir(parents=True, exist_ok=True)
    profile_payload = profile.to_dict() if profile else {}
    manifest_payload = manifest or _read_json(Path(batch.manifest_path) if batch and batch.manifest_path else root / "broker_file_manifest.json")
    roundtrip_payload = roundtrip or _read_json(root / "broker_file_roundtrip_report.json")
    summary = {
        "broker_file_batch_id": batch.file_batch_id if batch else "",
        "broker_file_gateway_status": batch.status if batch else "missing",
        "broker_file_order_count": batch.order_count if batch else 0,
        "broker_file_total_order_value": batch.total_order_value if batch else 0.0,
        "broker_file_roundtrip_error_count": int(roundtrip_payload.get("error_count", 0) or 0),
        "broker_file_missing_ack_count": int(roundtrip_payload.get("missing_ack_count", 0) or 0),
        "broker_file_orphan_fill_count": int(roundtrip_payload.get("orphan_fill_count", 0) or 0),
        "file_outbox_real_submit_detected": False,
        "no_real_submit": True,
    }
    paths = {
        "broker_file_gateway_report_path": str(root / "broker_file_gateway_report.json"),
        "broker_file_gateway_report_md_path": str(root / "broker_file_gateway_report.md"),
        "broker_file_events_path": str(root / "broker_file_events.jsonl"),
    }
    report = BrokerFileGatewayReport(
        file_batch_id=batch.file_batch_id if batch else "",
        status=batch.status if batch else "missing",
        profile=profile_payload,
        batch=batch.to_dict() if batch else {},
        manifest=manifest_payload,
        roundtrip=roundtrip_payload,
        paths=paths,
        summary=summary,
    )
    write_json_artifact(paths["broker_file_gateway_report_path"], report.to_dict(), artifact_type="broker_file_gateway_report", producer="broker_file_gateway")
    events = _read_jsonl(Path(store_dir) / "broker_file_events.jsonl")
    write_jsonl_artifact(paths["broker_file_events_path"], events, artifact_type="broker_file_events", producer="broker_file_gateway")
    Path(paths["broker_file_gateway_report_md_path"]).write_text(_markdown(report.to_dict()), encoding="utf-8")
    return {**report.to_dict(), "paths": paths}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    return "\n".join(
        [
            "# Broker File Gateway Report",
            "",
            f"- file_batch_id: `{payload.get('file_batch_id', '')}`",
            f"- status: `{payload.get('status', '')}`",
            f"- order_count: `{summary.get('broker_file_order_count', 0)}`",
            f"- roundtrip_error_count: `{summary.get('broker_file_roundtrip_error_count', 0)}`",
            f"- no_real_submit: `{summary.get('no_real_submit', True)}`",
            "",
        ]
    )
