"""Artifact writers for raw data sidecar indexes."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import utc_now, write_json_artifact, write_jsonl_artifact

from .models import RawDataIndexManifest, RawDataIndexReport, RawDataIndexValidationReport, RawDatasetIndex, RawPartitionRecord


def write_raw_data_index_artifacts(
    *,
    manifest: RawDataIndexManifest | None,
    dataset_indexes: list[RawDatasetIndex],
    partitions: list[RawPartitionRecord],
    validation: RawDataIndexValidationReport | None,
    issues: list[dict],
    output_dir: str | Path,
    data_dir: str | Path,
    status: str | None = None,
    write_tables: bool = True,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    if manifest is not None:
        manifest_payload = {
            **manifest.to_dict(),
            "dataset_indexes_path": str(root / "raw_dataset_indexes.jsonl"),
            "partitions_path": str(root / "raw_partitions.jsonl"),
            "issues_path": str(root / "raw_data_index_issues.jsonl"),
        }
        manifest = RawDataIndexManifest(**manifest_payload)
        paths["raw_data_index_manifest_path"] = str(
            write_json_artifact(root / "raw_data_index_manifest.json", manifest.to_dict(), "raw_data_index_manifest", "raw_data_index")
        )
    if write_tables:
        paths["raw_dataset_indexes_path"] = str(
            write_jsonl_artifact(root / "raw_dataset_indexes.jsonl", [item.to_dict() for item in dataset_indexes], "raw_dataset_indexes", "raw_data_index")
        )
        paths["raw_partitions_path"] = str(
            write_jsonl_artifact(root / "raw_partitions.jsonl", [item.to_dict() for item in partitions], "raw_partitions", "raw_data_index")
        )
        paths["raw_data_index_issues_path"] = str(
            write_jsonl_artifact(root / "raw_data_index_issues.jsonl", issues, "raw_data_index_issues", "raw_data_index")
        )
    if validation is not None:
        paths["raw_data_index_validation_report_path"] = str(
            write_json_artifact(
                root / "raw_data_index_validation_report.json",
                validation.to_dict(),
                "raw_data_index_validation_report",
                "raw_data_index",
            )
        )
    report = RawDataIndexReport(
        report_id=f"raw_data_index_{utc_now().replace(':', '').replace('-', '')}",
        status=status or (validation.status if validation else (manifest.status if manifest else "blocked")),
        data_dir=str(data_dir),
        output_dir=str(root),
        manifest_path=paths.get("raw_data_index_manifest_path"),
        validation_report_path=paths.get("raw_data_index_validation_report_path"),
        dataset_count=len(dataset_indexes),
        total_records=sum(item.record_count for item in dataset_indexes),
        total_size_bytes=sum(item.file_size_bytes for item in dataset_indexes),
        total_parse_errors=sum(item.parse_error_count for item in dataset_indexes),
        stale_dataset_count=validation.stale_dataset_count if validation else 0,
        missing_dataset_count=sum(1 for item in dataset_indexes if item.status == "missing"),
        active_run_blocked=any(item.get("code") == "active_backfill_state" for item in issues),
        issues=issues,
        summary={
            "raw_data_index_status": status or (validation.status if validation else (manifest.status if manifest else "blocked")),
            "raw_data_index_dataset_count": len(dataset_indexes),
            "raw_data_index_record_count": sum(item.record_count for item in dataset_indexes),
            "raw_data_index_size_gb": sum(item.file_size_bytes for item in dataset_indexes) / (1024**3),
            "raw_data_index_parse_error_count": sum(item.parse_error_count for item in dataset_indexes),
            "raw_data_index_stale_dataset_count": validation.stale_dataset_count if validation else 0,
            "raw_data_index_missing_core_count": 0,
            "raw_data_index_partition_count": len(partitions),
            "raw_data_index_hash": manifest.index_hash if manifest else None,
        },
        created_at=utc_now(),
    )
    paths["raw_data_index_report_path"] = str(
        write_json_artifact(root / "raw_data_index_report.json", report.to_dict(), "raw_data_index_report", "raw_data_index")
    )
    md_path = root / "raw_data_index_report.md"
    md_path.write_text(_markdown(report, dataset_indexes), encoding="utf-8")
    paths["raw_data_index_report_md_path"] = str(md_path)
    return paths


def dumps(payload: dict, pretty: bool = False) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty)


def _markdown(report: RawDataIndexReport, indexes: list[RawDatasetIndex]) -> str:
    lines = [
        "# Raw Data Index Report",
        "",
        f"- Status: `{report.status}`",
        f"- Dataset count: {report.dataset_count}",
        f"- Total records: {report.total_records}",
        f"- Total size GB: {report.total_size_bytes / (1024**3):.3f}",
        f"- Parse errors: {report.total_parse_errors}",
        f"- Stale datasets: {report.stale_dataset_count}",
        "",
        "| Dataset | Status | Records | Size MB | First | Last | Partitions |",
        "| --- | --- | ---: | ---: | --- | --- | ---: |",
    ]
    for item in indexes:
        lines.append(
            f"| {item.dataset} | {item.status} | {item.record_count} | {item.file_size_bytes / (1024**2):.2f} | {item.first_date or ''} | {item.last_date or ''} | {item.partition_count} |"
        )
    if report.issues:
        lines.extend(["", "## Issues"])
        lines.extend(f"- `{issue.get('severity')}` `{issue.get('code')}` {issue.get('dataset') or ''}: {issue.get('message')}" for issue in report.issues[:50])
    return "\n".join(lines) + "\n"
