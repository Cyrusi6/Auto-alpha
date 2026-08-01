"""Writers for risk control artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import RiskControlReport


def write_risk_control_report(
    report: RiskControlReport,
    output_dir: str | Path,
    accepted_orders: list[dict[str, Any]] | None = None,
    rejected_orders: list[dict[str, Any]] | None = None,
    clipped_orders: list[dict[str, Any]] | None = None,
) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "risk_control_report_path": root / "risk_control_report.json",
        "risk_control_report_md_path": root / "risk_control_report.md",
        "risk_control_breaches_path": root / "risk_control_breaches.jsonl",
        "risk_limit_usage_path": root / "risk_limit_usage.jsonl",
        "risk_control_decisions_path": root / "risk_control_decisions.jsonl",
        "accepted_orders_path": root / "accepted_orders.jsonl",
        "rejected_orders_path": root / "rejected_orders.jsonl",
        "clipped_orders_path": root / "clipped_orders.jsonl",
        "kill_switch_state_path": root / "kill_switch_state.json",
    }
    payload = report.to_dict()
    payload["paths"] = {key: str(path) for key, path in paths.items()}
    write_json_artifact(paths["risk_control_report_path"], payload, artifact_type="risk_control_report", producer="risk_controls")
    write_jsonl_artifact(paths["risk_control_breaches_path"], [item.to_dict() for item in report.breaches], artifact_type="risk_control_breaches", producer="risk_controls")
    write_jsonl_artifact(paths["risk_limit_usage_path"], [item.to_dict() for item in report.usage], artifact_type="risk_limit_usage", producer="risk_controls")
    write_jsonl_artifact(paths["risk_control_decisions_path"], [item.to_dict() for item in report.decisions], artifact_type="risk_control_decisions", producer="risk_controls")
    write_jsonl_artifact(paths["accepted_orders_path"], accepted_orders or [], artifact_type="risk_accepted_orders", producer="risk_controls")
    write_jsonl_artifact(paths["rejected_orders_path"], rejected_orders or [], artifact_type="risk_rejected_orders", producer="risk_controls")
    write_jsonl_artifact(paths["clipped_orders_path"], clipped_orders or [], artifact_type="risk_clipped_orders", producer="risk_controls")
    write_json_artifact(paths["kill_switch_state_path"], report.kill_switch, artifact_type="kill_switch_state", producer="risk_controls")
    paths["risk_control_report_md_path"].write_text(_markdown(payload), encoding="utf-8")
    return paths


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Risk Control Report",
        "",
        f"- policy: `{payload.get('policy_id')}`",
        f"- profile: `{payload.get('profile')}`",
        f"- status: `{payload.get('status')}`",
        f"- trade_date: `{payload.get('trade_date')}`",
        f"- accepted_orders: `{payload.get('accepted_orders')}`",
        f"- rejected_orders: `{payload.get('rejected_orders')}`",
        f"- clipped_orders: `{payload.get('clipped_orders')}`",
        f"- warning_count: `{payload.get('warning_count')}`",
        f"- error_count: `{payload.get('error_count')}`",
        f"- blocker_count: `{payload.get('blocker_count')}`",
        "",
        "| breach | severity | action | message |",
        "| --- | --- | --- | --- |",
    ]
    for breach in payload.get("breaches", [])[:50]:
        lines.append(
            f"| {breach.get('limit_id')} | {breach.get('severity')} | {breach.get('action')} | {str(breach.get('message', '')).replace('|', ' ')} |"
        )
    return "\n".join(lines) + "\n"
