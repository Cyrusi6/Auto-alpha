"""Production orchestrator artifact writers."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import ProductionReadinessReport, ProductionRunPlan, ProductionRunReport


def write_production_plan(plan: ProductionRunPlan, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = write_json_artifact(root / "production_run_plan.json", plan.to_dict(), "production_run_plan", "production_orchestrator")
    md_path = root / "production_run_plan.md"
    md_path.write_text("# Production Run Plan\n\n" + "\n".join(f"- {phase}" for phase in plan.phases) + "\n", encoding="utf-8")
    return {"production_run_plan_path": str(json_path), "production_run_plan_md_path": str(md_path)}


def write_production_report(
    report: ProductionRunReport,
    readiness: ProductionReadinessReport,
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    report_path = write_json_artifact(root / "production_orchestrator_report.json", payload, "production_orchestrator_report", "production_orchestrator")
    report_md = root / "production_orchestrator_report.md"
    report_md.write_text(_render_markdown(payload), encoding="utf-8")
    readiness_path = write_json_artifact(root / "production_readiness_report.json", readiness.to_dict(), "production_readiness_report", "production_orchestrator")
    phases_path = write_jsonl_artifact(root / "production_phase_runs.jsonl", [phase.to_dict() for phase in report.phase_runs], "production_phase_runs", "production_orchestrator")
    gates_path = write_jsonl_artifact(root / "production_gate_results.jsonl", [gate.to_dict() for gate in report.gate_results], "production_gate_results", "production_orchestrator")
    events_path = write_jsonl_artifact(root / "production_run_events.jsonl", [{"production_run_id": report.production_run_id, "event": "publish_report", "status": report.status}], "production_run_events", "production_orchestrator")
    runbook_path = write_json_artifact(root / "production_runbook.json", _runbook_payload(report), "production_runbook", "production_orchestrator")
    package_path = write_json_artifact(root / "production_day_package.json", _package_payload(report), "production_day_package", "production_orchestrator")
    return {
        "production_orchestrator_report_path": str(report_path),
        "production_orchestrator_report_md_path": str(report_md),
        "production_readiness_report_path": str(readiness_path),
        "production_phase_runs_path": str(phases_path),
        "production_gate_results_path": str(gates_path),
        "production_run_events_path": str(events_path),
        "production_runbook_path": str(runbook_path),
        "production_day_package_path": str(package_path),
    }


def _runbook_payload(report: ProductionRunReport) -> dict:
    return {
        "production_run_id": report.production_run_id,
        "steps": [
            {"step_id": "review_gates", "title": "Review Readiness Gates", "status": "pending" if report.status in {"blocked", "failed"} else "complete"},
            {"step_id": "resume_failed_phase", "title": "Resume Failed Phase", "status": "pending" if report.status in {"blocked", "failed"} else "skipped"},
            {"step_id": "close_day", "title": "Close Day", "status": "complete" if report.status == "closed" else "pending"},
        ],
    }


def _package_payload(report: ProductionRunReport) -> dict:
    return {
        "production_run_id": report.production_run_id,
        "trade_date": report.trade_date,
        "run_mode": report.run_mode,
        "status": report.status,
        "artifact_paths": report.artifact_paths,
        "gate_summary": report.readiness.get("summary", {}),
        "incident_summary": report.incident_summary,
    }


def _render_markdown(payload: dict) -> str:
    lines = [
        "# Production Orchestrator Report",
        "",
        f"- production_run_id: `{payload.get('production_run_id')}`",
        f"- trade_date: `{payload.get('trade_date')}`",
        f"- run_mode: `{payload.get('run_mode')}`",
        f"- status: `{payload.get('status')}`",
        "",
        "## Phases",
        "",
        "| phase | status | error |",
        "| --- | --- | --- |",
    ]
    for phase in payload.get("phase_runs", []):
        lines.append(f"| {phase.get('phase')} | {phase.get('status')} | {phase.get('error') or ''} |")
    lines.extend(["", "## Summary", "", "```json", json.dumps(payload.get("summary", {}), ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines) + "\n"
