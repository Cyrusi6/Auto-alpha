"""Report writers for local model registry artifacts."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from artifact_schema.writer import write_json_artifact

from .lineage import build_model_lineage_graph
from .models import ModelRegistryReport
from .store import LocalModelRegistry


def build_model_registry_report(registry: LocalModelRegistry, lineage_graph_path: str | None = None) -> ModelRegistryReport:
    manifest = registry.write_manifest()
    versions = registry.load_model_versions()
    deployments = registry.load_deployments()
    return ModelRegistryReport(
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        manifest=manifest.to_dict(),
        active_models=[record.to_dict() for record in versions if record.lifecycle_status == "active"],
        latest_models=[record.to_dict() for record in versions[-10:]],
        deployments=[record.to_dict() for record in deployments],
        recent_events=[event.to_dict() for event in registry.load_events()[-25:]],
        lineage_graph_path=lineage_graph_path,
    )


def write_model_registry_report(registry: LocalModelRegistry, output_dir: str | Path | None = None) -> tuple[Path, Path]:
    root = Path(output_dir) if output_dir is not None else registry.root_dir
    root.mkdir(parents=True, exist_ok=True)
    lineage = build_model_lineage_graph(registry)
    lineage_path = root / "model_lineage_graph.json"
    write_json_artifact(lineage_path, lineage.to_dict(), artifact_type="model_lineage_graph", producer="model_registry")
    report = build_model_registry_report(registry, str(lineage_path))
    json_path = root / "model_registry_report.json"
    md_path = root / "model_registry_report.md"
    write_json_artifact(json_path, report.to_dict(), artifact_type="model_registry_report", producer="model_registry")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    registry.write_manifest()
    return json_path, md_path


def write_lineage_graph(registry: LocalModelRegistry, graph, output_dir: str | Path | None = None) -> Path:
    root = Path(output_dir) if output_dir is not None else registry.root_dir
    root.mkdir(parents=True, exist_ok=True)
    path = root / "model_lineage_graph.json"
    write_json_artifact(path, graph.to_dict(), artifact_type="model_lineage_graph", producer="model_registry")
    return path


def _render_markdown(report: ModelRegistryReport) -> str:
    lines = [
        "# Model Registry Report",
        "",
        f"- model_versions: {report.manifest.get('model_versions', 0)}",
        f"- active_deployments: {report.manifest.get('active_deployments', 0)}",
        "",
        "## Active Models",
        "",
        "| model_version_id | kind | factor_id | status |",
        "| --- | --- | --- | --- |",
    ]
    for record in report.active_models:
        lines.append(
            f"| {record.get('model_version_id')} | {record.get('model_kind')} | {record.get('factor_id')} | {record.get('lifecycle_status')} |"
        )
    lines.extend(["", "## Status Counts", "", "```json", json.dumps(report.manifest.get("status_counts", {}), indent=2), "```", ""])
    return "\n".join(lines)
