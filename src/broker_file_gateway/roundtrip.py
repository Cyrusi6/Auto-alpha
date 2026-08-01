"""Round-trip checks for broker file dry-run batches."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import BrokerFileRoundTripIssue, BrokerFileRoundTripReport, BrokerFileBatchStatus
from .state import LocalBrokerFileGatewayStore


def run_file_roundtrip_check(
    *,
    store_dir: str | Path,
    outbox_dir: str | Path,
    normalized_dir: str | Path,
    output_dir: str | Path,
    file_batch_id: str | None = None,
    broker_batch_id: str = "",
) -> dict[str, Any]:
    outbox = Path(outbox_dir)
    norm = Path(normalized_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    orders = _read_jsonl(outbox / "broker_orders.jsonl")
    ack = _read_jsonl(norm / "normalized_broker_file_ack.jsonl")
    status_rows = _read_jsonl(norm / "normalized_broker_file_status.jsonl")
    fills = _read_jsonl(norm / "normalized_broker_file_fills.jsonl")
    rejects = _read_jsonl(norm / "normalized_broker_file_rejects.jsonl")
    order_ids = {str(row.get("client_order_id") or ""): row for row in orders}
    ack_ids = [str(row.get("client_order_id") or "") for row in ack]
    fill_ids = [str(row.get("broker_fill_id") or row.get("client_order_id") or "") for row in fills]
    issues: list[BrokerFileRoundTripIssue] = []
    for client_order_id in order_ids:
        if client_order_id not in ack_ids:
            issues.append(BrokerFileRoundTripIssue("error", "missing_ack", "order is missing ack", {"client_order_id": client_order_id}))
    for fill in fills:
        client_order_id = str(fill.get("client_order_id") or "")
        if client_order_id not in order_ids:
            issues.append(BrokerFileRoundTripIssue("error", "orphan_fill", "fill does not match any order", {"client_order_id": client_order_id}))
            continue
        order_value = float(order_ids[client_order_id].get("order_value") or 0.0)
        fill_value = float(fill.get("value") or fill.get("order_value") or 0.0)
        if fill_value - order_value > 1e-6:
            issues.append(BrokerFileRoundTripIssue("error", "fill_value_exceeds_order", "fill notional exceeds order notional", {"client_order_id": client_order_id, "fill_value": fill_value, "order_value": order_value}))
    duplicates = len(fill_ids) - len(set(fill_ids))
    for _ in range(max(duplicates, 0)):
        issues.append(BrokerFileRoundTripIssue("error", "duplicate_fill", "duplicate fill id detected"))
    unknown_status = [row for row in status_rows if str(row.get("status") or "").upper() not in {"ACK", "ACCEPTED", "FILLED", "PARTIAL", "PARTIAL_FILLED", "REJECTED", "CANCELLED"}]
    for row in unknown_status:
        issues.append(BrokerFileRoundTripIssue("error", "unknown_status", "unknown broker status", {"status": row.get("status")}))
    errors = sum(1 for issue in issues if issue.severity == "error")
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    report = BrokerFileRoundTripReport(
        file_batch_id=file_batch_id or "",
        broker_batch_id=broker_batch_id,
        status="passed" if errors == 0 else "failed",
        order_count=len(orders),
        ack_count=len(ack),
        status_count=len(status_rows),
        fill_count=len(fills),
        reject_count=len(rejects),
        missing_ack_count=sum(1 for issue in issues if issue.code == "missing_ack"),
        orphan_fill_count=sum(1 for issue in issues if issue.code == "orphan_fill"),
        duplicate_fill_count=sum(1 for issue in issues if issue.code == "duplicate_fill"),
        unknown_status_count=len(unknown_status),
        error_count=errors,
        warning_count=warnings,
        issues=issues,
        summary={"no_real_submit": True, "roundtrip_checked": True},
    )
    paths = {
        "broker_file_roundtrip_report_path": str(output / "broker_file_roundtrip_report.json"),
        "broker_file_roundtrip_report_md_path": str(output / "broker_file_roundtrip_report.md"),
        "broker_file_roundtrip_issues_path": str(output / "broker_file_roundtrip_issues.jsonl"),
    }
    write_json_artifact(paths["broker_file_roundtrip_report_path"], report.to_dict(), artifact_type="broker_file_roundtrip_report", producer="broker_file_gateway")
    write_jsonl_artifact(paths["broker_file_roundtrip_issues_path"], [issue.to_dict() for issue in issues], artifact_type="broker_file_roundtrip_issues", producer="broker_file_gateway")
    Path(paths["broker_file_roundtrip_report_md_path"]).write_text(_markdown(report), encoding="utf-8")
    store = LocalBrokerFileGatewayStore(store_dir)
    batch = store.load_batch(file_batch_id)
    if batch:
        store.update_batch(batch, status=BrokerFileBatchStatus.reconciled if errors == 0 else BrokerFileBatchStatus.failed, metadata={**batch.metadata, "roundtrip": report.to_dict()})
    return {"status": report.status, "file_batch_id": file_batch_id or "", "roundtrip": report.to_dict(), "paths": paths}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _markdown(report: BrokerFileRoundTripReport) -> str:
    lines = [
        "# Broker File Round-Trip Report",
        "",
        f"- file_batch_id: `{report.file_batch_id}`",
        f"- status: `{report.status}`",
        f"- order_count: `{report.order_count}`",
        f"- ack_count: `{report.ack_count}`",
        f"- fill_count: `{report.fill_count}`",
        f"- error_count: `{report.error_count}`",
        "",
        "| severity | code | message |",
        "| --- | --- | --- |",
    ]
    for issue in report.issues:
        lines.append(f"| {issue.severity} | {issue.code} | {issue.message} |")
    return "\n".join(lines) + "\n"
