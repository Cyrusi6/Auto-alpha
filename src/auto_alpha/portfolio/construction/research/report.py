"""Immutable result publication for portfolio auto_alpha.research.discovery.studies."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import attach_artifact_metadata, write_jsonl_artifact

from .contracts import SHADOW_CANDIDATE_STATUS, PortfolioResearchError, stable_hash


def publish_portfolio_research_result(result: dict[str, Any], output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root).resolve()
    generations = root / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    result_hash = str(result.get("content_hash") or stable_hash(result))
    generation_id = f"portfolio_research_{result_hash[:24]}"
    target = generations / generation_id
    if not target.exists():
        staging = Path(tempfile.mkdtemp(prefix=f".{generation_id}.", dir=generations))
        try:
            simulation_runs = list(result.get("simulation_runs") or [])
            report = {key: value for key, value in result.items() if key not in {"simulation_runs", "factor_weights", "windows"}}
            _write_artifact(staging / "portfolio_research_report.json", report, "portfolio_research_report")
            write_jsonl_artifact(
                staging / "portfolio_factor_weights.jsonl",
                result.get("factor_weights") or [],
                "portfolio_factor_weights",
                "portfolio_research",
            )
            write_jsonl_artifact(
                staging / "portfolio_walk_forward_windows.jsonl",
                result.get("windows") or [],
                "portfolio_walk_forward_windows",
                "portfolio_research",
            )
            simulation_catalog = []
            for run in simulation_runs:
                run_id = str((run.get("summary") or {}).get("run_id") or "")
                if not run_id:
                    raise PortfolioResearchError("portfolio_simulation_run_id_missing")
                safe_id = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:20]
                run_path = staging / "simulation_runs" / f"{safe_id}.json"
                _write_artifact(run_path, run, "portfolio_simulation_run")
                simulation_catalog.append(
                    {"run_id": run_id, "path": str(run_path.relative_to(staging)), "sha256": _sha256(run_path)}
                )
            _write_artifact(
                staging / "portfolio_simulation_catalog.json",
                {"run_count": len(simulation_catalog), "runs": simulation_catalog, "catalog_root": stable_hash(simulation_catalog)},
                "portfolio_simulation_catalog",
            )
            shadow_rows = []
            if result.get("status") == SHADOW_CANDIDATE_STATUS:
                shadow_rows.append(
                    {
                        "portfolio_research_content_hash": result_hash,
                        "status": "pending_independent_audit",
                        "shadow_only": True,
                        "paper_ready": False,
                        "live_ready": False,
                        "reason": "portfolio walk-forward passed; independent audit required before paper",
                    }
                )
            write_jsonl_artifact(
                staging / "portfolio_shadow_queue.jsonl",
                shadow_rows,
                "portfolio_shadow_queue",
                "portfolio_research",
            )
            manifest_core = {
                "status": result.get("status"),
                "result_content_hash": result_hash,
                "policy_id": result.get("policy_id"),
                "policy_hash": result.get("policy_hash"),
                "factor_certified_count": int(result.get("factor_certified_count") or 0),
                "walk_forward_window_count": int(result.get("walk_forward_window_count") or 0),
                "simulation_run_count": len(simulation_catalog),
                "shadow_queue_count": len(shadow_rows),
                "shadow_only": True,
                "independent_audit_required_for_paper": True,
                "certification_ready": False,
                "portfolio_ready": False,
                "paper_ready": False,
                "live_ready": False,
            }
            manifest = {
                **manifest_core,
                "content_hash": stable_hash(manifest_core),
                "generation_id": generation_id,
            }
            _write_artifact(staging / "portfolio_research_manifest.json", manifest, "portfolio_research_manifest")
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    manifest_path = target / "portfolio_research_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _validate_result_generation(target, manifest)
    pointer = {
        "generation_id": generation_id,
        "content_hash": manifest["content_hash"],
        "manifest": f"generations/{generation_id}/portfolio_research_manifest.json",
        "status": manifest["status"],
    }
    temporary = root / ".current.tmp"
    temporary.write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, root / "current.json")
    return {**manifest, "manifest_path": str(manifest_path), "generation_dir": str(target)}


def _validate_result_generation(root: Path, manifest: dict[str, Any]) -> None:
    required = {
        "portfolio_research_report.json",
        "portfolio_factor_weights.jsonl",
        "portfolio_walk_forward_windows.jsonl",
        "portfolio_simulation_catalog.json",
        "portfolio_shadow_queue.jsonl",
        "portfolio_research_manifest.json",
    }
    observed = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if not required.issubset(observed):
        raise PortfolioResearchError("portfolio_research_generation_incomplete")
    core = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id", "artifact_type", "producer", "created_at", "schema_version", "artifact_metadata"}}
    if stable_hash(core) != manifest.get("content_hash"):
        raise PortfolioResearchError("portfolio_research_manifest_hash_invalid")
    shadow_rows = _read_jsonl(root / "portfolio_shadow_queue.jsonl")
    if manifest.get("status") == SHADOW_CANDIDATE_STATUS and len(shadow_rows) != 1:
        raise PortfolioResearchError("portfolio_shadow_queue_missing")
    if manifest.get("status") != SHADOW_CANDIDATE_STATUS and shadow_rows:
        raise PortfolioResearchError("blocked_or_rejected_portfolio_entered_shadow_queue")


def _write_artifact(path, payload, artifact_type):
    path.parent.mkdir(parents=True, exist_ok=True)
    value = attach_artifact_metadata(dict(payload), artifact_type, "portfolio_research")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
