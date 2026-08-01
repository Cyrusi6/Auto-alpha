"""Broker connectivity report writers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .credentials import collect_credential_refs, write_credential_ref_manifest
from .models import BrokerConnectionProfile, BrokerConnectivityReport, BrokerConnectivitySession, BrokerNetworkGuard
from .network_guard import write_network_guard_report


def write_connectivity_artifacts(
    *,
    output_dir: str | Path,
    profile: BrokerConnectionProfile,
    guard: BrokerNetworkGuard,
    probe_result,
    session: BrokerConnectivitySession,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    refs = collect_credential_refs(profile)
    issues = [issue.to_dict() for issue in probe_result.issues]
    report = BrokerConnectivityReport(
        report_id=f"broker_connectivity_report_{_utc_id()}",
        created_at=_utc_now(),
        status=probe_result.status,
        profile=profile.to_dict(),
        credential_refs=[ref.to_dict() for ref in refs],
        network_guard=guard.to_dict(),
        probe_result=probe_result.to_dict(),
        session=session.to_dict(),
        summary={
            "profile_name": profile.profile_name,
            "broker_name": profile.broker_name,
            "connectivity_mode": profile.connectivity_mode,
            "network_guard_status": guard.status,
            "approved": guard.approved,
            "readonly_only": True,
            "real_submit_supported": False,
            "secret_blocker_count": 0,
            "issue_count": len(issues),
            "record_counts": dict(probe_result.record_counts),
            "prohibited_submit_blocked": bool(probe_result.readonly_enforcement.get("prohibited_submit_blocked")),
        },
        issues=issues,
        real_submit_supported=False,
    )
    paths = {
        "broker_connectivity_profile_path": root / "broker_connectivity_profile.json",
        "broker_credential_ref_manifest_path": root / "broker_credential_ref_manifest.json",
        "broker_network_guard_report_path": root / "broker_network_guard_report.json",
        "broker_connectivity_probe_report_path": root / "broker_connectivity_probe_report.json",
        "broker_connectivity_report_path": root / "broker_connectivity_report.json",
        "broker_connectivity_report_md_path": root / "broker_connectivity_report.md",
        "broker_connectivity_sessions_path": root / "broker_connectivity_sessions.jsonl",
        "broker_connectivity_events_path": root / "broker_connectivity_events.jsonl",
        "broker_connectivity_issues_path": root / "broker_connectivity_issues.jsonl",
    }
    write_json_artifact(paths["broker_connectivity_profile_path"], profile.to_dict(), "broker_connectivity_profile", "broker_connectivity")
    write_credential_ref_manifest(paths["broker_credential_ref_manifest_path"], profile)
    write_network_guard_report(paths["broker_network_guard_report_path"], profile, guard)
    write_json_artifact(paths["broker_connectivity_probe_report_path"], probe_result.to_dict(), "broker_connectivity_probe_report", "broker_connectivity")
    write_json_artifact(paths["broker_connectivity_report_path"], report.to_dict(), "broker_connectivity_report", "broker_connectivity")
    write_jsonl_artifact(paths["broker_connectivity_sessions_path"], [session.to_dict()], "broker_connectivity_sessions", "broker_connectivity")
    write_jsonl_artifact(paths["broker_connectivity_events_path"], [{"event_id": f"broker_conn_report_{_utc_id()}", "event_type": "report_written", "session_id": session.session_id, "created_at": _utc_now()}], "broker_connectivity_events", "broker_connectivity")
    write_jsonl_artifact(paths["broker_connectivity_issues_path"], issues, "broker_connectivity_issues", "broker_connectivity")
    paths["broker_connectivity_report_md_path"].write_text(_render_markdown(report.to_dict()), encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# Broker Connectivity Report",
        "",
        f"- status: `{payload.get('status')}`",
        f"- profile_name: `{summary.get('profile_name')}`",
        f"- broker_name: `{summary.get('broker_name')}`",
        f"- connectivity_mode: `{summary.get('connectivity_mode')}`",
        f"- network_guard_status: `{summary.get('network_guard_status')}`",
        f"- readonly_only: `{summary.get('readonly_only')}`",
        f"- real_submit_supported: `{summary.get('real_submit_supported')}`",
        f"- secret_blocker_count: `{summary.get('secret_blocker_count')}`",
        "",
        "## Issues",
        "",
        "| severity | code | message |",
        "| --- | --- | --- |",
    ]
    for issue in payload.get("issues", []):
        lines.append(f"| {issue.get('severity')} | {issue.get('code')} | {issue.get('message')} |")
    return "\n".join(lines) + "\n"


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _utc_id() -> str:
    return _utc_now().replace("-", "").replace(":", "").replace("Z", "")

