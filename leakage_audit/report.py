"""Writers for leakage audit artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import LeakageAuditReport


def write_leakage_audit_report(report: LeakageAuditReport, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "leakage_audit_report_path": root / "leakage_audit_report.json",
        "leakage_audit_report_md_path": root / "leakage_audit_report.md",
        "leakage_issues_path": root / "leakage_issues.jsonl",
        "formula_leakage_scan_path": root / "formula_leakage_scan.json",
        "truncation_consistency_report_path": root / "truncation_consistency_report.json",
        "factor_value_leakage_report_path": root / "factor_value_leakage_report.json",
        "backtest_leakage_report_path": root / "backtest_leakage_report.json",
    }
    payload = report.to_dict()
    write_json_artifact(paths["leakage_audit_report_path"], payload, "leakage_audit_report", "leakage_audit")
    Path(paths["leakage_audit_report_md_path"]).write_text(_render_markdown(payload), encoding="utf-8")
    write_jsonl_artifact(paths["leakage_issues_path"], [issue.to_dict() for issue in report.issues], "leakage_issues", "leakage_audit")
    write_json_artifact(paths["formula_leakage_scan_path"], report.formula_scan.to_dict(), "formula_leakage_scan", "leakage_audit")
    write_json_artifact(paths["truncation_consistency_report_path"], report.truncation_consistency.to_dict(), "truncation_consistency_report", "leakage_audit")
    write_json_artifact(paths["factor_value_leakage_report_path"], report.factor_value_leakage.to_dict(), "factor_value_leakage_report", "leakage_audit")
    write_json_artifact(paths["backtest_leakage_report_path"], report.backtest_leakage.to_dict(), "backtest_leakage_report", "leakage_audit")
    return {key: str(path) for key, path in paths.items()}


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Leakage Audit Report",
        "",
        f"- status: `{payload.get('status')}`",
        f"- blocker_count: `{payload.get('blocker_count', 0)}`",
        f"- error_count: `{payload.get('error_count', 0)}`",
        f"- warning_count: `{payload.get('warning_count', 0)}`",
        f"- leakage_gate_status: `{payload.get('leakage_gate_status')}`",
        "",
        "## Checks",
        "",
        f"- formula_scan: `{(payload.get('formula_scan') or {}).get('scanned_formula_count', 0)}` formulas",
        f"- truncation_passed: `{(payload.get('truncation_consistency') or {}).get('passed')}`",
        f"- factor_future_date_count: `{(payload.get('factor_value_leakage') or {}).get('future_date_count', 0)}`",
        f"- backtest_gate: `{(payload.get('backtest_leakage') or {}).get('leakage_gate_status', '')}`",
        "",
        "## Issues",
        "",
        "| severity | code | artifact | key | message |",
        "| --- | --- | --- | --- | --- |",
    ]
    for issue in payload.get("issues", []):
        lines.append(f"| {issue.get('severity')} | {issue.get('code')} | {issue.get('artifact') or ''} | {issue.get('key') or ''} | {issue.get('message')} |")
    return "\n".join(lines) + "\n"
