"""Build lightweight model lineage graphs from local artifacts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from factor_store import LocalFactorStore

from .models import ModelLineageGraph
from .store import LocalModelRegistry


def build_model_lineage_graph(
    registry: LocalModelRegistry,
    factor_store: LocalFactorStore | None = None,
    artifact_catalog_paths: list[str] | None = None,
    artifact_dirs: list[str] | None = None,
) -> ModelLineageGraph:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    warnings: list[str] = []
    factors = {record.factor_id: record for record in (factor_store.load_factors() if factor_store is not None else [])}
    for model in registry.load_model_versions():
        nodes.append({"id": model.model_version_id, "type": "model_version", "status": model.lifecycle_status, "factor_id": model.factor_id})
        nodes.append({"id": model.factor_id, "type": "factor", "factor_type": model.factor_type})
        edges.append({"source": model.factor_id, "target": model.model_version_id, "type": "registered_as"})
        for parent in model.parent_factor_ids:
            nodes.append({"id": parent, "type": "parent_factor"})
            edges.append({"source": parent, "target": model.factor_id, "type": "derived_from"})
        factor = factors.get(model.factor_id)
        if factor and factor.batch_id:
            nodes.append({"id": factor.batch_id, "type": "research_batch"})
            edges.append({"source": factor.batch_id, "target": model.factor_id, "type": "promoted_by"})
    for deployment in registry.load_deployments():
        nodes.append({"id": deployment.deployment_id, "type": "deployment", "status": deployment.status, "environment": deployment.environment})
        edges.append({"source": deployment.model_version_id, "target": deployment.deployment_id, "type": "deployed_as"})
        if deployment.rollback_from_deployment_id:
            edges.append({"source": deployment.rollback_from_deployment_id, "target": deployment.deployment_id, "type": "rolled_back_from"})
    for catalog_path in artifact_catalog_paths or []:
        try:
            from research_suite.catalog import load_artifact_catalog

            catalog = load_artifact_catalog(catalog_path)
        except Exception as exc:
            warnings.append(f"catalog_unreadable:{catalog_path}:{exc}")
            continue
        catalog_id = f"catalog:{Path(catalog_path).name}"
        nodes.append({"id": catalog_id, "type": "artifact_catalog", "path": catalog_path})
        for entry in catalog.entries:
            nodes.append({"id": entry.path, "type": entry.stage, "name": entry.name, "kind": entry.kind})
            edges.append({"source": entry.path, "target": catalog_id, "type": "listed_in"})
    for artifact_dir in artifact_dirs or []:
        path = Path(artifact_dir)
        if not path.exists():
            warnings.append(f"artifact_dir_missing:{artifact_dir}")
            continue
        nodes.append({"id": str(path), "type": "artifact_dir"})
        for filename, node_type in _SETTLEMENT_ARTIFACT_TYPES.items():
            artifact_path = path / filename
            if artifact_path.exists():
                nodes.append({"id": str(artifact_path), "type": node_type, "path": str(artifact_path)})
                edges.append({"source": str(artifact_path), "target": str(path), "type": "contained_in"})
    return ModelLineageGraph(
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        nodes=_dedupe_nodes(nodes),
        edges=edges,
        warnings=warnings,
    )


def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(node.get("id"))
        if node_id in seen:
            continue
        seen.add(node_id)
        result.append(node)
    return result


_SETTLEMENT_ARTIFACT_TYPES = {
    "settlement_report.json": "settlement_report",
    "account_reconciliation_report.json": "account_reconciliation",
    "account_nav.jsonl": "account_nav",
    "cash_buckets.jsonl": "cash_buckets",
    "realized_pnl.jsonl": "realized_pnl",
}
