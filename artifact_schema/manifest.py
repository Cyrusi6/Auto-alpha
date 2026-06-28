"""Build artifact schema manifests with checksums."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import ArtifactCompatibilityMode, ArtifactManifest, ArtifactManifestEntry
from .registry import get_definition, infer_artifact_type
from .writer import write_json_artifact


def build_artifact_manifest(paths: Iterable[str | Path], root_dir: str | Path | None = None) -> ArtifactManifest:
    root = Path(root_dir) if root_dir is not None else None
    entries = [_entry(Path(path), root) for path in paths if Path(path).exists() and Path(path).is_file()]
    return ArtifactManifest(
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        root_dir=str(root) if root else None,
        entries=entries,
    )


def write_artifact_manifest(manifest: ArtifactManifest, output_dir: str | Path) -> tuple[Path, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "artifact_schema_manifest.json"
    md_path = target / "artifact_schema_manifest.md"
    write_json_artifact(json_path, manifest.to_dict(), artifact_type="artifact_schema_manifest", producer="artifact_schema")
    md_path.write_text(_markdown(manifest), encoding="utf-8")
    return json_path, md_path


def _entry(path: Path, root: Path | None) -> ArtifactManifestEntry:
    payload = _read_json(path) if path.suffix == ".json" else {}
    sidecar = _read_json(Path(f"{path}.schema.json")) if path.suffix == ".jsonl" else {}
    artifact_type = payload.get("artifact_type") or sidecar.get("artifact_type") or infer_artifact_type(path)
    definition = get_definition(artifact_type)
    version = payload.get("schema_version") or sidecar.get("schema_version") or (definition.schema_version if definition else None)
    compatibility = ArtifactCompatibilityMode.strict if payload.get("artifact_type") or sidecar else ArtifactCompatibilityMode.legacy
    relative = str(path.relative_to(root)) if root and path.is_relative_to(root) else str(path)
    return ArtifactManifestEntry(
        path=str(path),
        relative_path=relative,
        artifact_type=artifact_type,
        schema_version=version,
        compatibility_mode=compatibility,
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
        record_count=_record_count(path) if path.suffix == ".jsonl" else None,
        created_at=payload.get("created_at") or sidecar.get("created_at"),
        producer=payload.get("producer") or sidecar.get("producer"),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _markdown(manifest: ArtifactManifest) -> str:
    lines = [
        "# Artifact Schema Manifest",
        "",
        f"- entries: `{len(manifest.entries)}`",
        "",
        "| path | type | version | mode | bytes | records | sha256 |",
        "| --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for entry in manifest.entries:
        lines.append(
            f"| `{entry.relative_path}` | {entry.artifact_type or ''} | {entry.schema_version or ''} | {entry.compatibility_mode} | {entry.size_bytes} | {entry.record_count if entry.record_count is not None else ''} | `{entry.sha256[:12]}` |"
        )
    return "\n".join(lines) + "\n"
