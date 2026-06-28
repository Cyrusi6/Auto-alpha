"""CLI for local data lake versioning and research freezes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from artifact_schema.writer import utc_now

from .fingerprint import content_hash_for_fingerprints, fingerprint_data_dir
from .freeze import create_research_freeze, validate_freeze, write_freeze_validation_report
from .lineage import build_data_lineage_graph, write_data_lineage_graph
from .models import DatasetVersionRecord
from .registry import LocalDataLakeRegistry
from .report import write_data_lake_report, write_dataset_version_manifest, write_research_freeze
from .retention import write_retention_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local A-share data lake versions and freezes.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["create-version", "create-freeze", "validate-version", "validate-freeze", "list-versions", "list-freezes", "lineage", "report", "retire-freeze", "smoke"]:
        _add_common(sub.add_parser(name))
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir")
    parser.add_argument("--registry-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--freeze-dir")
    parser.add_argument("--freeze-name")
    parser.add_argument("--freeze-mode", choices=["copy", "hardlink", "manifest_only"], default="copy")
    parser.add_argument("--dataset-version-id")
    parser.add_argument("--data-freeze-id")
    parser.add_argument("--provider", default="sample")
    parser.add_argument("--start-date", default="20240102")
    parser.add_argument("--end-date", default="20240104")
    parser.add_argument("--datasets")
    parser.add_argument("--quality-report-path")
    parser.add_argument("--dataset-stats-path")
    parser.add_argument("--api-audit-path")
    parser.add_argument("--backfill-run-report-path")
    parser.add_argument("--backfill-coverage-report-path")
    parser.add_argument("--data-source-smoke-report-path")
    parser.add_argument("--pit-validation-report-path")
    parser.add_argument("--leakage-audit-report-path")
    parser.add_argument("--corporate-actions-report-path")
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--artifact-dir", action="append", default=[])
    parser.add_argument("--artifact-catalog-path", action="append", default=[])
    parser.add_argument("--require-pit", action="store_true")
    parser.add_argument("--require-leakage", action="store_true")
    parser.add_argument("--require-quality-ok", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    registry = LocalDataLakeRegistry(args.registry_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "create-version" or args.command == "smoke":
        if not args.data_dir:
            raise SystemExit("--data-dir is required")
        version = _create_version(args, registry)
        write_dataset_version_manifest(version, output_dir)
        write_data_lake_report(registry, output_dir)
        payload = {"status": "ok", "dataset_version_id": version.dataset_version_id, "content_hash": version.content_hash, "paths": {"dataset_version_manifest_path": str(output_dir / "dataset_version_manifest.json"), "data_lake_report_path": str(output_dir / "data_lake_report.json")}}
    elif args.command == "create-freeze":
        version = _version_from_args(args, registry)
        if version is None:
            raise SystemExit("dataset version not found")
        freeze = create_research_freeze(
            args.data_dir or version.data_dir,
            args.freeze_dir or output_dir / "research_freeze",
            version,
            args.freeze_name or "research_freeze",
            mode=args.freeze_mode,
            artifact_paths=_artifact_paths(args),
            matrix_cache_dir=args.matrix_cache_dir,
        )
        freeze = registry.register_freeze(freeze)
        write_research_freeze(freeze, output_dir)
        write_data_lake_report(registry, output_dir)
        payload = {"status": "ok", "freeze_id": freeze.freeze_id, "dataset_version_id": freeze.dataset_version_id, "paths": {"research_data_freeze_path": str(output_dir / "research_data_freeze.json"), "freeze_dir": freeze.freeze_dir, "data_lake_report_path": str(output_dir / "data_lake_report.json")}}
    elif args.command == "validate-freeze":
        if not args.freeze_dir:
            raise SystemExit("--freeze-dir is required")
        report = validate_freeze(args.freeze_dir)
        write_freeze_validation_report(report, output_dir)
        payload = {"status": report.status, "freeze_id": report.freeze_id, "error_count": report.error_count, "warning_count": report.warning_count, "paths": {"freeze_validation_report_path": str(output_dir / "freeze_validation_report.json")}}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 1 if args.fail_on_error and report.error_count else 0
    elif args.command == "lineage":
        graph = build_data_lineage_graph(
            registry_dir=args.registry_dir,
            freeze_dir=args.freeze_dir,
            artifact_dirs=args.artifact_dir,
            artifact_catalog_paths=args.artifact_catalog_path,
        )
        path = write_data_lineage_graph(graph, output_dir)
        payload = {"status": "ok", "nodes": len(graph.nodes), "edges": len(graph.edges), "warnings": graph.warnings, "paths": {"data_lineage_graph_path": str(path)}}
    elif args.command == "list-versions":
        payload = {"versions": [record.to_dict() for record in registry.list_versions()]}
    elif args.command == "list-freezes":
        payload = {"freezes": [record.to_dict() for record in registry.list_freezes()]}
    elif args.command == "report":
        json_path, md_path = write_data_lake_report(registry, output_dir)
        retention_path = write_retention_report(registry, output_dir)
        payload = {"status": "ok", "paths": {"data_lake_report_path": str(json_path), "data_lake_report_md_path": str(md_path), "data_retention_report_path": str(retention_path)}}
    else:
        payload = {"status": "skipped", "message": "retire-freeze is report-only in local mode"}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0


def _create_version(args: argparse.Namespace, registry: LocalDataLakeRegistry) -> DatasetVersionRecord:
    datasets = [item.strip() for item in args.datasets.split(",") if item.strip()] if args.datasets else None
    fingerprints = fingerprint_data_dir(args.data_dir, datasets)
    content_hash = content_hash_for_fingerprints(fingerprints)
    digest_payload = {"provider": args.provider, "data_dir": str(args.data_dir), "content_hash": content_hash}
    version_id = f"dsver_{hashlib.sha256(json.dumps(digest_payload, sort_keys=True).encode('utf-8')).hexdigest()[:16]}"
    version = DatasetVersionRecord(
        dataset_version_id=version_id,
        provider=args.provider,
        data_dir=str(args.data_dir),
        start_date=args.start_date,
        end_date=args.end_date,
        datasets=[item.dataset for item in fingerprints],
        dataset_fingerprints=[item.to_dict() for item in fingerprints],
        quality_report_path=args.quality_report_path,
        dataset_stats_path=args.dataset_stats_path,
        api_audit_path=args.api_audit_path,
        backfill_run_report_path=args.backfill_run_report_path,
        backfill_coverage_report_path=args.backfill_coverage_report_path,
        pit_validation_report_path=args.pit_validation_report_path,
        leakage_audit_report_path=args.leakage_audit_report_path,
        corporate_actions_report_path=args.corporate_actions_report_path,
        data_source_smoke_report_path=args.data_source_smoke_report_path,
        created_at=utc_now(),
        status="validated",
        content_hash=content_hash,
        metadata={},
    )
    return registry.register_dataset_version(version)


def _version_from_args(args: argparse.Namespace, registry: LocalDataLakeRegistry) -> DatasetVersionRecord | None:
    if args.dataset_version_id:
        return registry.get_dataset_version(args.dataset_version_id)
    return registry.latest_dataset_version(provider=args.provider, status="validated")


def _artifact_paths(args: argparse.Namespace) -> dict[str, str | None]:
    return {
        "quality_report_path": args.quality_report_path,
        "dataset_stats_path": args.dataset_stats_path,
        "api_audit_path": args.api_audit_path,
        "backfill_run_report_path": args.backfill_run_report_path,
        "backfill_coverage_report_path": args.backfill_coverage_report_path,
        "data_source_smoke_report_path": args.data_source_smoke_report_path,
        "pit_validation_report_path": args.pit_validation_report_path,
        "leakage_audit_report_path": args.leakage_audit_report_path,
        "corporate_actions_report_path": args.corporate_actions_report_path,
    }


if __name__ == "__main__":
    raise SystemExit(main())
