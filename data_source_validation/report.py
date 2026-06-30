"""Report writers for data source smoke validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .contracts import DATASET_CONTRACTS
from .models import DataSourceSmokeReport, IncrementalRecoveryResult


def write_data_source_smoke_report(report: DataSourceSmokeReport, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    paths = {
        "data_source_smoke_report_path": root / "data_source_smoke_report.json",
        "data_source_smoke_report_md_path": root / "data_source_smoke_report.md",
        "provider_probe_path": root / "provider_probe.json",
        "field_coverage_path": root / "field_coverage.json",
        "audit_summary_path": root / "audit_summary.json",
        "incremental_recovery_report_path": root / "incremental_recovery_report.json",
        "baseline_compare_summary_path": root / "baseline_compare_summary.json",
        "dataset_contracts_path": root / "dataset_contracts.json",
        "provider_readiness_matrix_path": root / "provider_readiness_matrix.json",
        "api_permission_matrix_path": root / "api_permission_matrix.json",
        "required_dataset_status_path": root / "required_dataset_status.json",
    }
    _write_json(paths["data_source_smoke_report_path"], payload, "data_source_smoke_report")
    paths["data_source_smoke_report_md_path"].write_text(_render_smoke_markdown(payload), encoding="utf-8")
    _write_json(paths["provider_probe_path"], {"probes": payload.get("provider_probe", [])}, "provider_probe")
    _write_json(paths["field_coverage_path"], {"datasets": payload.get("field_coverage", [])}, "field_coverage")
    _write_json(paths["audit_summary_path"], payload.get("audit_summary", {}) or {}, "audit_summary")
    _write_json(paths["incremental_recovery_report_path"], payload.get("incremental_recovery", {}) or {}, "incremental_recovery_report")
    _write_json(paths["baseline_compare_summary_path"], payload.get("baseline_compare", {}) or {}, "baseline_compare_summary")
    _write_json(paths["dataset_contracts_path"], {"datasets": [contract.to_dict() for contract in DATASET_CONTRACTS.values()]}, "dataset_contracts")
    _write_json(paths["provider_readiness_matrix_path"], _provider_readiness_matrix(payload), "provider_readiness_matrix")
    _write_json(paths["api_permission_matrix_path"], _api_permission_matrix(payload), "api_permission_matrix")
    _write_json(paths["required_dataset_status_path"], _required_dataset_status(payload), "required_dataset_status")
    return {name: str(path) for name, path in paths.items()}


def _provider_readiness_matrix(payload: dict[str, Any]) -> dict[str, Any]:
    diagnostics = payload.get("provider_probe", [])
    return {
        "provider": payload.get("provider"),
        "status": payload.get("status"),
        "token_redaction_check": _token_redaction_check(payload),
        "rows": [
            {
                "dataset": item.get("dataset"),
                "api_name": item.get("api_name"),
                "status": item.get("status"),
                "diagnostic_code": item.get("diagnostic_code"),
                "records": item.get("records", 0),
            }
            for item in diagnostics
        ],
    }


def _api_permission_matrix(payload: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in payload.get("provider_probe", []):
        code = item.get("diagnostic_code")
        rows.append(
            {
                "api_name": item.get("api_name"),
                "dataset": item.get("dataset"),
                "permission_status": "blocked" if code in {"permission_denied", "invalid_token"} else "rate_limited" if code == "rate_limited" else "ok" if item.get("status") == "OK" else "unknown",
                "diagnostic_code": code,
                "missing_fields": item.get("missing_fields", []),
            }
        )
    return {"rows": rows, "permission_issue_count": sum(row["permission_status"] in {"blocked", "rate_limited"} for row in rows)}


def _required_dataset_status(payload: dict[str, Any]) -> dict[str, Any]:
    datasets = payload.get("datasets", [])
    return {
        "datasets": [
            {
                "dataset": item.get("dataset"),
                "status": item.get("status"),
                "records": item.get("records", 0),
                "quality_errors": item.get("quality_errors", 0),
                "quality_warnings": item.get("quality_warnings", 0),
            }
            for item in datasets
        ],
        "missing_required_dataset_count": sum(int(item.get("records", 0) or 0) <= 0 for item in datasets),
    }


def _token_redaction_check(payload: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False)
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    bad = ["TUSHARE_TOKEN=", "fake-token-redacted"]
    return {"passed": not any(item in text for item in bad), "provider": payload.get("provider"), "allow_network": config.get("allow_network")}


def write_incremental_recovery_report(result: IncrementalRecoveryResult, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "incremental_recovery_report.json"
    md_path = root / "incremental_recovery_report.md"
    payload = result.to_dict()
    _write_json(json_path, payload, "incremental_recovery_report")
    lines = [
        "# Incremental Recovery Report",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- successful_job_count: `{payload.get('successful_job_count')}`",
        f"- failed_job_count: `{payload.get('failed_job_count')}`",
        f"- cache_hit_count: `{payload.get('cache_hit_count')}`",
        "",
        "## Duplicate Keys After",
        "",
        "```json",
        json.dumps(payload.get("duplicate_counts_after", {}), ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def _write_json(path: Path, payload: Any, artifact_type: str) -> None:
    if isinstance(payload, dict):
        write_json_artifact(path, payload, artifact_type=artifact_type, producer="data_source_validation")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _render_smoke_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Data Source Smoke Report",
        "",
        f"- provider: `{payload.get('provider')}`",
        f"- status: `{payload.get('status')}`",
        f"- diagnostics: `{payload.get('diagnostic_counts', {})}`",
        "",
        "## Provider Probe",
        "",
        "| dataset | api | status | code | records | message |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for item in payload.get("provider_probe", []):
        lines.append(
            f"| {item.get('dataset')} | {item.get('api_name')} | {item.get('status')} | {item.get('diagnostic_code') or ''} | {item.get('records', 0)} | {item.get('message', '')} |"
        )
    lines.extend(
        [
            "",
            "## Dataset Results",
            "",
            "| dataset | status | records | quality errors | quality warnings | message |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for item in payload.get("datasets", []):
        lines.append(
            f"| {item.get('dataset')} | {item.get('status')} | {item.get('records', 0)} | {item.get('quality_errors', 0)} | {item.get('quality_warnings', 0)} | {item.get('message', '')} |"
        )
    lines.extend(
        [
            "",
            "## Field Coverage",
            "",
            "| dataset | records | coverage | missing fields | duplicate keys | date range |",
            "| --- | ---: | ---: | --- | ---: | --- |",
        ]
    )
    for item in payload.get("field_coverage", []):
        date_range = f"{item.get('first_date') or ''}..{item.get('last_date') or ''}"
        lines.append(
            f"| {item.get('dataset')} | {item.get('records', 0)} | {float(item.get('field_coverage_ratio', 0.0)):.2f} | {', '.join(item.get('missing_fields', []))} | {item.get('duplicate_key_count', 0)} | {date_range} |"
        )
    audit = payload.get("audit_summary") or {}
    incremental = payload.get("incremental_recovery") or {}
    baseline = payload.get("baseline_compare") or {}
    lines.extend(
        [
            "",
            "## Audit And Cache",
            "",
            f"- total_requests: `{audit.get('total_requests', 0)}`",
            f"- failed_requests: `{audit.get('failed_requests', 0)}`",
            f"- cache_hit_rate: `{audit.get('cache_hit_rate', 0.0)}`",
            "",
            "## Incremental Recovery",
            "",
            f"- ok: `{incremental.get('ok')}`",
            f"- successful_job_count: `{incremental.get('successful_job_count')}`",
            f"- failed_job_count: `{incremental.get('failed_job_count')}`",
            "",
            "## Baseline Compare",
            "",
            f"- compared: `{baseline.get('compared', False)}`",
            f"- has_differences: `{baseline.get('has_differences', False)}`",
            f"- difference_count: `{baseline.get('difference_count', 0)}`",
        ]
    )
    return "\n".join(lines) + "\n"
