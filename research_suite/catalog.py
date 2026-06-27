"""Artifact catalog helpers for research suites."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import ArtifactCatalog, ArtifactEntry


def register_artifact(
    catalog: ArtifactCatalog,
    name: str,
    path: str | Path,
    kind: str,
    stage: str,
    metadata: dict[str, Any] | None = None,
) -> ArtifactCatalog:
    entry = ArtifactEntry(name=name, path=str(path), kind=kind, stage=stage, metadata=metadata)
    return ArtifactCatalog(suite_name=catalog.suite_name, created_at=catalog.created_at, entries=[*catalog.entries, entry])


def write_artifact_catalog(catalog: ArtifactCatalog, output_dir: str | Path) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "artifact_catalog.json"
    md_path = output_path / "artifact_catalog.md"
    json_path.write_text(json.dumps(catalog.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(catalog), encoding="utf-8")
    return json_path, md_path


def load_artifact_catalog(path: str | Path) -> ArtifactCatalog:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ArtifactCatalog(
        suite_name=payload["suite_name"],
        created_at=payload["created_at"],
        entries=[ArtifactEntry(**entry) for entry in payload.get("entries", [])],
    )


def _render_markdown(catalog: ArtifactCatalog) -> str:
    lines = [
        "# Artifact Catalog",
        "",
        f"- suite_name: `{catalog.suite_name}`",
        f"- created_at: `{catalog.created_at}`",
        "",
        "| name | kind | stage | path |",
        "| --- | --- | --- | --- |",
    ]
    for entry in catalog.entries:
        lines.append(f"| {entry.name} | {entry.kind} | {entry.stage} | `{entry.path}` |")
    lines.append("")
    return "\n".join(lines)
