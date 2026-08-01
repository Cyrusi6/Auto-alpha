"""CLI for local release gate and manifest generation."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from artifact_schema.manifest import build_artifact_manifest, write_artifact_manifest
from artifact_schema.scanner import paths_from_artifact_catalog, scan_artifact_dirs

from .gates import run_release_gates, utc_now
from .inventory import build_cli_inventory, build_dependency_inventory, build_module_inventory
from .models import ReleaseConfig, ReleaseManifest
from .report import summarize_files, write_release_outputs


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate local release artifacts and release gate reports.")
    parser.add_argument("--release-name", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--artifact-dir", action="append", default=[])
    parser.add_argument("--artifact-catalog-path", action="append", default=[])
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--pytest-args", default="")
    parser.add_argument("--run-build", action="store_true")
    parser.add_argument("--run-import-smoke", action="store_true")
    parser.add_argument("--run-dashboard-import", action="store_true")
    parser.add_argument("--run-schema-validation", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--fail-on-gate-error", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path.cwd()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = ReleaseConfig(
        release_name=args.release_name,
        output_dir=str(output_dir),
        artifact_dirs=list(args.artifact_dir),
        artifact_catalog_paths=list(args.artifact_catalog_path),
        run_tests=bool(args.run_tests),
        pytest_args=str(args.pytest_args or ""),
        run_build=bool(args.run_build),
        run_import_smoke=bool(args.run_import_smoke),
        run_dashboard_import=bool(args.run_dashboard_import),
        run_schema_validation=bool(args.run_schema_validation),
        allow_network=bool(args.allow_network),
    )
    dependency_inventory = build_dependency_inventory(root)
    module_inventory = build_module_inventory(root)
    cli_inventory = build_cli_inventory(root)
    gate_report = run_release_gates(config, root)
    artifact_paths = _artifact_paths(config)
    schema_manifest_paths: tuple[Path | None, Path | None] = (None, None)
    if args.run_schema_validation:
        schema_manifest = build_artifact_manifest(artifact_paths)
        schema_manifest_paths = write_artifact_manifest(schema_manifest, output_dir)
    dist_paths = [
        path
        for path in sorted(Path("dist").glob("*"))
        if path.suffix == ".whl" or path.name.endswith(".tar.gz")
    ]
    build_artifacts = summarize_files(dist_paths, "dist") if args.run_build else []
    manifest = ReleaseManifest(
        release_name=args.release_name,
        created_at=utc_now(),
        git_commit=_git_output(["git", "rev-parse", "HEAD"]),
        git_branch=_git_output(["git", "branch", "--show-current"]),
        config=config.to_dict(),
        artifacts=summarize_files(artifact_paths, "artifact"),
        build_artifacts=build_artifacts,
        paths={
            "artifact_schema_manifest_path": str(schema_manifest_paths[0]) if schema_manifest_paths[0] else "",
            "artifact_schema_manifest_md_path": str(schema_manifest_paths[1]) if schema_manifest_paths[1] else "",
        },
    )
    paths = write_release_outputs(
        manifest,
        dependency_inventory,
        module_inventory,
        cli_inventory,
        gate_report,
        output_dir,
    )
    payload = {
        "release_name": args.release_name,
        "status": gate_report.status,
        "error_count": gate_report.error_count,
        "warning_count": gate_report.warning_count,
        "paths": {key: str(path) for key, path in paths.items()},
        "build_artifacts": [item.to_dict() for item in build_artifacts],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 1 if args.fail_on_gate_error and gate_report.error_count else 0


def _artifact_paths(config: ReleaseConfig) -> list[Path]:
    paths: list[Path] = []
    paths.extend(scan_artifact_dirs(config.artifact_dirs))
    for catalog in config.artifact_catalog_paths:
        paths.extend(paths_from_artifact_catalog(catalog))
    return sorted(set(Path(path) for path in paths if Path(path).exists()))


def _git_output(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


if __name__ == "__main__":
    raise SystemExit(main())
