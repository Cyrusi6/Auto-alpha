"""Writers for broker statement import artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import BrokerStatementImportResult


def write_statement_import_report(result: BrokerStatementImportResult, output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = write_json_artifact(
        root / "broker_statement_manifest.json",
        result.manifest.to_dict(),
        artifact_type="broker_statement_manifest",
        producer="broker_statement",
    )
    report_path = write_json_artifact(
        root / "broker_statement_import_report.json",
        {
            "statement_id": result.statement_id,
            "status": result.status,
            "schema_name": result.manifest.schema_name,
            "account_id": result.manifest.account_id,
            "broker_name": result.manifest.broker_name,
            "trade_date": result.manifest.trade_date,
            "as_of_date": result.manifest.as_of_date,
            "record_counts": result.manifest.record_counts,
            "parse_issue_count": result.manifest.parse_issue_count,
            "warning_count": result.manifest.warning_count,
            "synthetic": result.synthetic,
            "paths": result.paths,
        },
        artifact_type="broker_statement_import_report",
        producer="broker_statement",
    )
    validation_path = write_json_artifact(
        root / "broker_statement_validation_report.json",
        result.validation.to_dict(),
        artifact_type="broker_statement_validation_report",
        producer="broker_statement",
    )
    issues_path = write_jsonl_artifact(
        root / "broker_statement_parse_issues.jsonl",
        [issue.to_dict() for issue in result.validation.issues],
        artifact_type="broker_statement_parse_issues",
        producer="broker_statement",
    )
    md_path = root / "broker_statement_import_report.md"
    md_path.write_text(_markdown(result), encoding="utf-8")
    return {
        "broker_statement_manifest_path": manifest_path,
        "broker_statement_import_report_path": report_path,
        "broker_statement_validation_report_path": validation_path,
        "broker_statement_parse_issues_path": issues_path,
        "broker_statement_import_report_md_path": md_path,
    }


def _markdown(result: BrokerStatementImportResult) -> str:
    manifest = result.manifest
    lines = [
        "# Broker Statement Import Report",
        "",
        f"- statement_id: `{result.statement_id}`",
        f"- status: `{result.status}`",
        f"- schema: `{manifest.schema_name}`",
        f"- account_id: `{manifest.account_id}`",
        f"- broker_name: `{manifest.broker_name}`",
        f"- trade_date: `{manifest.trade_date}`",
        f"- as_of_date: `{manifest.as_of_date}`",
        f"- synthetic: `{result.synthetic}`",
        "",
        "## Record Counts",
        "",
        "| dataset | records |",
        "| --- | ---: |",
    ]
    for dataset, count in sorted(manifest.record_counts.items()):
        lines.append(f"| {dataset} | {count} |")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- errors: `{result.validation.error_count}`",
            f"- warnings: `{result.validation.warning_count}`",
        ]
    )
    return "\n".join(lines) + "\n"
