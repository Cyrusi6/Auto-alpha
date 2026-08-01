"""Writers for point-in-time governance artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .contracts import PIT_DATASET_CONTRACTS
from .models import ActiveSecurityMask, PITDatasetManifest, PITValidationReport, SecurityLifecycleRecord, SurvivorshipBiasReport


def write_pit_artifacts(
    output_dir: str | Path,
    report: PITValidationReport,
    survivorship: SurvivorshipBiasReport,
    lifecycle: list[SecurityLifecycleRecord],
    active_mask: list[ActiveSecurityMask],
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    contracts_payload = {"datasets": [contract.to_dict() for contract in PIT_DATASET_CONTRACTS.values()]}
    manifest = PITDatasetManifest(
        generated_at=report.generated_at,
        data_dir=report.data_dir,
        datasets=[
            {
                "dataset": name,
                "records": summary.get("records", 0),
                "timing": summary.get("timing"),
                "point_in_time_safe_by_default": summary.get("point_in_time_safe_by_default"),
            }
            for name, summary in sorted(report.dataset_summaries.items())
        ],
    )
    paths = {
        "pit_dataset_contracts_path": root / "pit_dataset_contracts.json",
        "pit_dataset_manifest_path": root / "pit_dataset_manifest.json",
        "pit_validation_report_path": root / "pit_validation_report.json",
        "pit_validation_report_md_path": root / "pit_validation_report.md",
        "security_lifecycle_path": root / "security_lifecycle.jsonl",
        "active_security_mask_path": root / "active_security_mask.jsonl",
        "survivorship_bias_report_path": root / "survivorship_bias_report.json",
        "survivorship_bias_report_md_path": root / "survivorship_bias_report.md",
    }
    write_json_artifact(paths["pit_dataset_contracts_path"], contracts_payload, "pit_dataset_contracts", "point_in_time")
    write_json_artifact(paths["pit_dataset_manifest_path"], manifest.to_dict(), "pit_dataset_manifest", "point_in_time")
    write_json_artifact(paths["pit_validation_report_path"], report.to_dict(), "pit_validation_report", "point_in_time")
    Path(paths["pit_validation_report_md_path"]).write_text(_render_validation_markdown(report.to_dict()), encoding="utf-8")
    write_jsonl_artifact(paths["security_lifecycle_path"], [item.to_dict() for item in lifecycle], "security_lifecycle", "point_in_time")
    write_jsonl_artifact(paths["active_security_mask_path"], [item.to_dict() for item in active_mask], "active_security_mask", "point_in_time")
    write_json_artifact(paths["survivorship_bias_report_path"], survivorship.to_dict(), "survivorship_bias_report", "point_in_time")
    Path(paths["survivorship_bias_report_md_path"]).write_text(_render_survivorship_markdown(survivorship.to_dict()), encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}


def _render_validation_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Point-In-Time Validation Report",
        "",
        f"- status: `{payload.get('status')}`",
        f"- blocker_count: `{payload.get('blocker_count', 0)}`",
        f"- error_count: `{payload.get('error_count', 0)}`",
        f"- warning_count: `{payload.get('warning_count', 0)}`",
        f"- feature_cutoff_mode: `{payload.get('feature_cutoff_mode')}`",
        f"- active_universe_coverage: `{payload.get('active_universe_coverage', 0.0)}`",
        "",
        "## Issues",
        "",
        "| severity | code | dataset | key | message |",
        "| --- | --- | --- | --- | --- |",
    ]
    for issue in payload.get("issues", []):
        lines.append(
            f"| {issue.get('severity')} | {issue.get('code')} | {issue.get('dataset') or ''} | {issue.get('key') or ''} | {issue.get('message')} |"
        )
    return "\n".join(lines) + "\n"


def _render_survivorship_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Survivorship Bias Report",
        "",
        f"- current_only_security_master: `{payload.get('current_only_security_master')}`",
        f"- securities_total: `{payload.get('securities_total', 0)}`",
        f"- listed_count: `{payload.get('listed_count', 0)}`",
        f"- delisted_count: `{payload.get('delisted_count', 0)}`",
        f"- paused_count: `{payload.get('paused_count', 0)}`",
        "",
        "```json",
        json.dumps(payload.get("warnings", []), ensure_ascii=False, indent=2),
        "```",
    ]
    return "\n".join(lines) + "\n"
