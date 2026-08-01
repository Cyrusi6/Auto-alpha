"""Build simple data lineage graphs across data lake artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import utc_now, write_json_artifact

from .models import DataLineageGraph


def build_data_lineage_graph(
    registry_dir: str | Path | None = None,
    freeze_dir: str | Path | None = None,
    artifact_dirs: list[str | Path] | None = None,
    artifact_catalog_paths: list[str | Path] | None = None,
) -> DataLineageGraph:
    nodes: list[dict[str, object]] = []
    edges: list[dict[str, object]] = []
    warnings: list[str] = []
    version_id: str | None = None
    freeze_id: str | None = None
    if freeze_dir:
        freeze_path = Path(freeze_dir) / "research_data_freeze.json"
        if freeze_path.exists():
            payload = json.loads(freeze_path.read_text(encoding="utf-8"))
            freeze_id = payload.get("freeze_id")
            version_id = payload.get("dataset_version_id")
            nodes.append({"id": freeze_id, "type": "research_freeze", "path": str(freeze_path)})
            if version_id:
                nodes.append({"id": version_id, "type": "dataset_version"})
                edges.append({"source": version_id, "target": freeze_id, "type": "frozen_as"})
        else:
            warnings.append("research_data_freeze.json missing")
    for catalog_path in artifact_catalog_paths or []:
        path = Path(catalog_path)
        if not path.exists():
            warnings.append(f"artifact catalog missing: {path}")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        catalog_id = f"catalog:{path}"
        nodes.append({"id": catalog_id, "type": "artifact_catalog", "path": str(path)})
        if freeze_id:
            edges.append({"source": freeze_id, "target": catalog_id, "type": "used_by"})
    for artifact_dir in artifact_dirs or []:
        path = Path(artifact_dir)
        if path.exists():
            node_id = f"artifact_dir:{path}"
            nodes.append({"id": node_id, "type": "artifact_dir", "path": str(path)})
            if freeze_id:
                edges.append({"source": freeze_id, "target": node_id, "type": "used_by"})
        else:
            warnings.append(f"artifact dir missing: {path}")
    return DataLineageGraph(created_at=utc_now(), nodes=nodes, edges=edges, warnings=warnings)


def write_data_lineage_graph(graph: DataLineageGraph, output_dir: str | Path) -> Path:
    return write_json_artifact(Path(output_dir) / "data_lineage_graph.json", graph.to_dict(), "data_lineage_graph", "data_lake")
