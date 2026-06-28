"""Release artifact writers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from artifact_schema.writer import write_json_artifact

from .models import (
    CliInventory,
    DependencyInventory,
    ModuleInventory,
    ReleaseArtifactSummary,
    ReleaseGateReport,
    ReleaseManifest,
)


def summarize_files(paths: Iterable[str | Path], kind: str) -> list[ReleaseArtifactSummary]:
    summaries: list[ReleaseArtifactSummary] = []
    for value in paths:
        path = Path(value)
        if not path.exists() or not path.is_file():
            continue
        summaries.append(
            ReleaseArtifactSummary(
                path=str(path),
                size_bytes=path.stat().st_size,
                sha256=_sha256(path),
                kind=kind,
            )
        )
    return summaries


def write_release_outputs(
    manifest: ReleaseManifest,
    dependency_inventory: DependencyInventory,
    module_inventory: ModuleInventory,
    cli_inventory: CliInventory,
    gate_report: ReleaseGateReport,
    output_dir: str | Path,
) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "release_manifest_path": root / "release_manifest.json",
        "release_manifest_md_path": root / "release_manifest.md",
        "dependency_inventory_path": root / "dependency_inventory.json",
        "module_inventory_path": root / "module_inventory.json",
        "cli_inventory_path": root / "cli_inventory.json",
        "release_gate_report_path": root / "release_gate_report.json",
        "release_gate_report_md_path": root / "release_gate_report.md",
        "release_notes_draft_path": root / "release_notes_draft.md",
    }
    write_json_artifact(paths["release_manifest_path"], manifest.to_dict(), artifact_type="release_manifest", producer="release_manager")
    paths["release_manifest_md_path"].write_text(_manifest_markdown(manifest), encoding="utf-8")
    write_json_artifact(paths["dependency_inventory_path"], dependency_inventory.to_dict(), artifact_type="dependency_inventory", producer="release_manager")
    write_json_artifact(paths["module_inventory_path"], module_inventory.to_dict(), artifact_type="module_inventory", producer="release_manager")
    write_json_artifact(paths["cli_inventory_path"], cli_inventory.to_dict(), artifact_type="cli_inventory", producer="release_manager")
    write_json_artifact(paths["release_gate_report_path"], gate_report.to_dict(), artifact_type="release_gate_report", producer="release_manager")
    paths["release_gate_report_md_path"].write_text(_gate_markdown(gate_report), encoding="utf-8")
    paths["release_notes_draft_path"].write_text(_release_notes(manifest, gate_report), encoding="utf-8")
    return paths


def _manifest_markdown(manifest: ReleaseManifest) -> str:
    lines = [
        "# Release Manifest",
        "",
        f"- release_name: `{manifest.release_name}`",
        f"- git_commit: `{manifest.git_commit}`",
        f"- git_branch: `{manifest.git_branch}`",
        f"- build_artifacts: `{len(manifest.build_artifacts)}`",
        "",
        "| path | kind | bytes | sha256 |",
        "| --- | --- | ---: | --- |",
    ]
    for item in [*manifest.artifacts, *manifest.build_artifacts]:
        lines.append(f"| `{item.path}` | {item.kind} | {item.size_bytes} | `{item.sha256[:12]}` |")
    return "\n".join(lines) + "\n"


def _gate_markdown(report: ReleaseGateReport) -> str:
    lines = [
        "# Release Gate Report",
        "",
        f"- status: `{report.status}`",
        f"- errors: `{report.error_count}`",
        f"- warnings: `{report.warning_count}`",
        "",
        "| check | status | duration | message |",
        "| --- | --- | ---: | --- |",
    ]
    for check in report.checks:
        lines.append(f"| {check.name} | {check.status} | {check.duration_seconds:.3f} | {check.message} |")
    return "\n".join(lines) + "\n"


def _release_notes(manifest: ReleaseManifest, gate_report: ReleaseGateReport) -> str:
    return "\n".join(
        [
            f"# Release Notes Draft: {manifest.release_name}",
            "",
            "## Gate Summary",
            "",
            f"- status: `{gate_report.status}`",
            f"- errors: `{gate_report.error_count}`",
            f"- warnings: `{gate_report.warning_count}`",
            "",
            "## Build Artifacts",
            "",
            *[f"- `{item.path}` ({item.size_bytes} bytes, sha256 `{item.sha256}`)" for item in manifest.build_artifacts],
            "",
            "## Notes",
            "",
            "- Offline release gate only; real Tushare checks remain manual and gated.",
            "- No live broker integration is enabled by this release workflow.",
            "",
        ]
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
