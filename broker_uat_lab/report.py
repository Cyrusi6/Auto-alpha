"""Broker UAT artifact writers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import BrokerAdapterContractReport, BrokerUatPlan, BrokerUatReport


def write_broker_uat_artifacts(
    *,
    output_dir: str | Path,
    plan: BrokerUatPlan,
    contract_report: BrokerAdapterContractReport,
    replay_report: dict[str, Any] | None = None,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    replay_report = replay_report or {"status": "skipped"}
    report = BrokerUatReport(
        uat_id=f"broker_uat_{_utc_id()}",
        created_at=_utc_now(),
        status=contract_report.status,
        plan=plan.to_dict(),
        contract_report=contract_report.to_dict(),
        replay_report=replay_report,
        summary={
            "scenario_count": contract_report.scenario_count,
            "failed_count": contract_report.failed_count,
            "warning_count": contract_report.warning_count,
            "real_network_required": False,
            "real_broker_credentials_required": False,
        },
    )
    paths = {
        "broker_uat_plan_path": root / "broker_uat_plan.json",
        "broker_uat_report_path": root / "broker_uat_report.json",
        "broker_uat_report_md_path": root / "broker_uat_report.md",
        "broker_uat_scenarios_path": root / "broker_uat_scenarios.jsonl",
        "broker_uat_results_path": root / "broker_uat_results.jsonl",
        "broker_adapter_capability_manifest_path": root / "broker_adapter_capability_manifest.json",
        "broker_adapter_contract_report_path": root / "broker_adapter_contract_report.json",
        "broker_uat_replay_report_path": root / "broker_uat_replay_report.json",
        "broker_uat_issues_path": root / "broker_uat_issues.jsonl",
    }
    write_json_artifact(paths["broker_uat_plan_path"], plan.to_dict(), "broker_uat_plan", "broker_uat_lab")
    write_json_artifact(paths["broker_uat_report_path"], report.to_dict(), "broker_uat_report", "broker_uat_lab")
    write_jsonl_artifact(paths["broker_uat_scenarios_path"], [scenario.to_dict() for scenario in plan.scenarios], "broker_uat_scenarios", "broker_uat_lab")
    write_jsonl_artifact(paths["broker_uat_results_path"], contract_report.results, "broker_uat_results", "broker_uat_lab")
    write_json_artifact(paths["broker_adapter_capability_manifest_path"], contract_report.capability_manifest, "broker_adapter_capability_manifest", "broker_uat_lab")
    write_json_artifact(paths["broker_adapter_contract_report_path"], contract_report.to_dict(), "broker_adapter_contract_report", "broker_uat_lab")
    write_json_artifact(paths["broker_uat_replay_report_path"], replay_report, "broker_uat_replay_report", "broker_uat_lab")
    write_jsonl_artifact(paths["broker_uat_issues_path"], contract_report.issues, "broker_uat_issues", "broker_uat_lab")
    paths["broker_uat_report_md_path"].write_text(_render_markdown(report.to_dict()), encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# BrokerAdapter UAT Report",
        "",
        f"- status: `{payload.get('status')}`",
        f"- scenario_count: `{summary.get('scenario_count', 0)}`",
        f"- failed_count: `{summary.get('failed_count', 0)}`",
        f"- warning_count: `{summary.get('warning_count', 0)}`",
        f"- real_network_required: `{summary.get('real_network_required')}`",
        f"- real_broker_credentials_required: `{summary.get('real_broker_credentials_required')}`",
        "",
        "| scenario | status | message |",
        "| --- | --- | --- |",
    ]
    for result in payload.get("contract_report", {}).get("results", []):
        lines.append(f"| {result.get('scenario_id')} | {result.get('status')} | {result.get('message')} |")
    return "\n".join(lines) + "\n"


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _utc_id() -> str:
    return _utc_now().replace("-", "").replace(":", "").replace("Z", "")
