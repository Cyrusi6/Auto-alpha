"""Read-only broker mirror artifact writers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import BrokerReadonlyMirrorReport, BrokerReadonlySnapshot
from .reconciliation import mirror_to_statement_artifacts


def write_readonly_mirror_artifacts(
    *,
    output_dir: str | Path,
    snapshot: BrokerReadonlySnapshot,
    reconciliation_report: dict[str, Any] | None = None,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    reconciliation_report = reconciliation_report or {
        "snapshot_id": snapshot.snapshot_id,
        "status": "skipped",
        "break_count": 0,
        "summary": {"readonly_mirror_break_count": 0},
        "issues": [],
        "real_submit_supported": False,
    }
    paths = {
        "broker_readonly_snapshot_path": root / "broker_readonly_snapshot.json",
        "broker_readonly_mirror_report_path": root / "broker_readonly_mirror_report.json",
        "broker_readonly_mirror_report_md_path": root / "broker_readonly_mirror_report.md",
        "readonly_broker_cash_path": root / "readonly_broker_cash.jsonl",
        "readonly_broker_positions_path": root / "readonly_broker_positions.jsonl",
        "readonly_broker_orders_path": root / "readonly_broker_orders.jsonl",
        "readonly_broker_fills_path": root / "readonly_broker_fills.jsonl",
        "readonly_broker_statements_path": root / "readonly_broker_statements.jsonl",
        "readonly_mirror_reconciliation_report_path": root / "readonly_mirror_reconciliation_report.json",
        "readonly_mirror_reconciliation_issues_path": root / "readonly_mirror_reconciliation_issues.jsonl",
    }
    report = BrokerReadonlyMirrorReport(
        report_id=f"readonly_mirror_report_{_utc_id()}",
        created_at=_utc_now(),
        status=snapshot.status,
        snapshot=snapshot.to_dict(),
        summary={
            "broker_name": snapshot.broker_name,
            "account_id": snapshot.account_id,
            "readonly_snapshot_status": snapshot.status,
            "readonly_cash_count": 1 if snapshot.cash else 0,
            "readonly_position_count": len(snapshot.positions),
            "readonly_order_count": len(snapshot.orders),
            "readonly_fill_count": len(snapshot.fills),
            "readonly_statement_count": len(snapshot.statements),
            "readonly_mirror_break_count": int(reconciliation_report.get("break_count", 0) or 0),
            "real_submit_supported": False,
        },
        paths={key: str(value) for key, value in paths.items()},
        issues=list(snapshot.issues) + list(reconciliation_report.get("issues", [])),
        real_submit_supported=False,
    )
    write_json_artifact(paths["broker_readonly_snapshot_path"], snapshot.to_dict(), "broker_readonly_snapshot", "broker_readonly_mirror")
    write_json_artifact(paths["broker_readonly_mirror_report_path"], report.to_dict(), "broker_readonly_mirror_report", "broker_readonly_mirror")
    write_jsonl_artifact(paths["readonly_broker_cash_path"], [snapshot.cash] if snapshot.cash else [], "readonly_broker_cash", "broker_readonly_mirror")
    write_jsonl_artifact(paths["readonly_broker_positions_path"], snapshot.positions, "readonly_broker_positions", "broker_readonly_mirror")
    write_jsonl_artifact(paths["readonly_broker_orders_path"], snapshot.orders, "readonly_broker_orders", "broker_readonly_mirror")
    write_jsonl_artifact(paths["readonly_broker_fills_path"], snapshot.fills, "readonly_broker_fills", "broker_readonly_mirror")
    write_jsonl_artifact(paths["readonly_broker_statements_path"], snapshot.statements, "readonly_broker_statements", "broker_readonly_mirror")
    write_json_artifact(paths["readonly_mirror_reconciliation_report_path"], reconciliation_report, "readonly_mirror_reconciliation_report", "broker_readonly_mirror")
    write_jsonl_artifact(paths["readonly_mirror_reconciliation_issues_path"], reconciliation_report.get("issues", []), "readonly_mirror_reconciliation_issues", "broker_readonly_mirror")
    statement_paths = mirror_to_statement_artifacts(snapshot, root)
    paths.update({key: Path(value) for key, value in statement_paths.items()})
    paths["broker_readonly_mirror_report_md_path"].write_text(_render_markdown(report.to_dict()), encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# Broker Read-Only Mirror Report",
        "",
        f"- status: `{payload.get('status')}`",
        f"- broker_name: `{summary.get('broker_name')}`",
        f"- account_id: `{summary.get('account_id')}`",
        f"- readonly_position_count: `{summary.get('readonly_position_count', 0)}`",
        f"- readonly_order_count: `{summary.get('readonly_order_count', 0)}`",
        f"- readonly_fill_count: `{summary.get('readonly_fill_count', 0)}`",
        f"- readonly_mirror_break_count: `{summary.get('readonly_mirror_break_count', 0)}`",
        f"- real_submit_supported: `{summary.get('real_submit_supported')}`",
    ]
    return "\n".join(lines) + "\n"


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _utc_id() -> str:
    return _utc_now().replace("-", "").replace(":", "").replace("Z", "")

