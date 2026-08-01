"""CLI for validating local artifact schemas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .manifest import build_artifact_manifest, write_artifact_manifest
from .report import build_validation_report, write_validation_report
from .scanner import paths_from_artifact_catalog, scan_artifact_dirs
from .validator import validate_artifact


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate local JSON/JSONL artifact schemas.")
    parser.add_argument("--artifact-dir", action="append", default=[])
    parser.add_argument("--artifact-catalog-path", action="append", default=[])
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    parser.add_argument("--include-unknown", action="store_true")
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--sample-jsonl-records", type=int)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    paths = []
    paths.extend(scan_artifact_dirs(args.artifact_dir))
    for catalog in args.artifact_catalog_path:
        paths.extend(paths_from_artifact_catalog(catalog))
    paths = sorted(set(Path(path) for path in paths))
    results = [
        validate_artifact(path, strict=args.strict, sample_jsonl_records=args.sample_jsonl_records)
        for path in paths
    ]
    if not args.include_unknown:
        results = [result for result in results if result.artifact_type is not None]
    report = build_validation_report(results)
    json_path, md_path, issues_path = write_validation_report(report, args.output_dir)
    manifest_paths: tuple[Path | None, Path | None] = (None, None)
    if args.write_manifest:
        manifest = build_artifact_manifest(paths)
        manifest_paths = write_artifact_manifest(manifest, args.output_dir)
    payload = report.to_dict() | {
        "paths": {
            "artifact_validation_report_path": str(json_path),
            "artifact_validation_report_md_path": str(md_path),
            "artifact_validation_issues_path": str(issues_path),
            "artifact_schema_manifest_path": str(manifest_paths[0]) if manifest_paths[0] else None,
            "artifact_schema_manifest_md_path": str(manifest_paths[1]) if manifest_paths[1] else None,
        }
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 1 if args.fail_on_error and report.error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
