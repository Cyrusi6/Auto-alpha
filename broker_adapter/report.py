"""Broker adapter report writers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import BrokerReconciliationReport
from .store import LocalBrokerStore


def write_broker_report(
    store: LocalBrokerStore,
    batch_id: str,
    reconciliation: BrokerReconciliationReport | None,
    output_dir: str | Path,
) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    orders = [record.to_dict() for record in store.load_orders(batch_id=batch_id)]
    events = [record.to_dict() for record in store.load_events(batch_id=batch_id)]
    fills = [record.to_dict() for record in store.load_fills(batch_id=batch_id)]
    summary = store.write_batch_summary(batch_id).to_dict()
    report = {
        "batch_id": batch_id,
        "summary": summary,
        "orders": orders,
        "fills": fills,
        "events": events,
        "reconciliation": reconciliation.to_dict() if reconciliation else {},
    }
    paths = {
        "broker_report_path": root / "broker_report.json",
        "broker_report_md_path": root / "broker_report.md",
        "broker_orders_path": root / "broker_orders.jsonl",
        "broker_events_path": root / "broker_events.jsonl",
        "broker_fills_path": root / "broker_fills.jsonl",
        "broker_reconciliation_path": root / "broker_reconciliation.json",
        "broker_reconciliation_md_path": root / "broker_reconciliation.md",
    }
    write_json_artifact(paths["broker_report_path"], report, artifact_type="broker_report", producer="broker_adapter")
    write_jsonl_artifact(paths["broker_orders_path"], orders, artifact_type="broker_orders", producer="broker_adapter")
    write_jsonl_artifact(paths["broker_events_path"], events, artifact_type="broker_events", producer="broker_adapter")
    write_jsonl_artifact(paths["broker_fills_path"], fills, artifact_type="broker_fills", producer="broker_adapter")
    recon_payload = reconciliation.to_dict() if reconciliation else {}
    write_json_artifact(paths["broker_reconciliation_path"], recon_payload, artifact_type="broker_reconciliation", producer="broker_adapter")
    paths["broker_report_md_path"].write_text(_markdown_report(report), encoding="utf-8")
    paths["broker_reconciliation_md_path"].write_text(_markdown_reconciliation(recon_payload), encoding="utf-8")
    return paths


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Broker Report",
        "",
        f"- batch_id: `{report.get('batch_id')}`",
        f"- submitted_orders: `{summary.get('submitted_orders', 0)}`",
        f"- filled_orders: `{summary.get('filled_orders', 0)}`",
        f"- partial_orders: `{summary.get('partial_orders', 0)}`",
        f"- rejected_orders: `{summary.get('rejected_orders', 0)}`",
        f"- open_orders: `{summary.get('open_orders', 0)}`",
        f"- unfilled_value: `{summary.get('unfilled_value', 0.0)}`",
    ]
    return "\n".join(lines) + "\n"


def _markdown_reconciliation(payload: dict[str, Any]) -> str:
    lines = [
        "# Broker Reconciliation",
        "",
        f"- batch_id: `{payload.get('batch_id', '')}`",
        f"- expected_child_orders: `{payload.get('expected_child_orders', 0)}`",
        f"- submitted_orders: `{payload.get('submitted_orders', 0)}`",
        f"- orphan_fills: `{payload.get('orphan_fills', 0)}`",
        f"- missing_fills: `{payload.get('missing_fills', 0)}`",
        f"- status_mismatch_count: `{payload.get('status_mismatch_count', 0)}`",
        "",
        "| severity | code | message |",
        "| --- | --- | --- |",
    ]
    for issue in payload.get("issues", []) if isinstance(payload.get("issues"), list) else []:
        if not isinstance(issue, dict):
            continue
        lines.append(f"| {issue.get('severity', '')} | {issue.get('code', '')} | {issue.get('message', '')} |")
    return "\n".join(lines) + "\n"
