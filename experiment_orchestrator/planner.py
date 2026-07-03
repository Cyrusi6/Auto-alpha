"""Experiment plan builder that emits compute job specs."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact
from compute_cluster.models import ComputeDeviceType, ComputeJobKind, ComputeJobSpec
from model_core.vocab import FORMULA_VOCAB

from .models import ExperimentGraphEdge, ExperimentGraphNode, ExperimentPlan, ExperimentStage
from .sharding import shard_formula_corpus


def create_experiment_plan(config: dict[str, Any]) -> ExperimentPlan:
    output_dir = Path(config.get("output_dir") or "artifacts/experiment")
    output_dir.mkdir(parents=True, exist_ok=True)
    experiment_id = str(config.get("experiment_id") or f"exp_compute_{uuid.uuid4().hex[:12]}")
    workflow = str(config.get("workflow") or "full_research_compute_smoke")
    if workflow in {"real_data_alpha_factory_large_plan", "real_data_alpha_factory_4gpu_template", "alpha_factory_campaign_warehouse_smoke"}:
        return _create_alpha_large_plan(config, output_dir, experiment_id, workflow)
    if workflow in {"validation_campaign_plan", "validation_campaign_smoke", "real_data_validation_campaign_large_plan"}:
        return _create_validation_large_plan(config, output_dir, experiment_id, workflow)
    if workflow in {
        "factor_certification_campaign_plan",
        "portfolio_certification_campaign_plan",
        "production_candidate_bundle_plan",
        "real_data_portfolio_campaign_large_plan",
    }:
        return _create_production_candidate_plan(config, output_dir, experiment_id, workflow)
    shard_count = max(1, int(config.get("shard_count") or 1))
    formula_corpus_path = config.get("formula_corpus_path")
    shards = []
    if formula_corpus_path:
        shards = shard_formula_corpus(formula_corpus_path, shard_count, output_dir / "shards")
    else:
        for idx in range(shard_count):
            shards.append(
                shard_formula_corpus(_write_inline_corpus(output_dir, idx), 1, output_dir / "shards" / f"inline_{idx}")[0]
            )
    jobs: list[ComputeJobSpec] = []
    nodes: list[ExperimentGraphNode] = []
    edges: list[ExperimentGraphEdge] = []
    device = str(config.get("device") or "auto")
    gpu_count = int(config.get("gpu_count") or 0)
    required = ComputeDeviceType.CUDA if device == "cuda" and gpu_count > 0 else ComputeDeviceType.CPU
    data_dir = str(config.get("data_dir") or (Path(config.get("data_freeze_dir", "")) / "data"))
    factor_store_base = Path(config.get("factor_store_dir") or output_dir / "factor_store_shards")
    matrix_cache_dir = config.get("matrix_cache_dir")
    max_formulas = config.get("max_formulas")
    for shard in shards:
        shard_output = output_dir / f"batch_eval_shard_{shard.shard_id}"
        job_id = f"{experiment_id}_batch_eval_shard_{shard.shard_id}"
        command = [
            sys.executable,
            "-m",
            "formula_batch_eval.run_batch_eval",
            "--data-dir",
            data_dir,
            "--factor-store-dir",
            str(factor_store_base / f"shard_{shard.shard_id}"),
            "--report-dir",
            str(shard_output / "reports"),
            "--output-dir",
            str(shard_output),
            "--corpus-path",
            str(shard.input_path),
            "--chunk-size",
            str(config.get("batch_eval_chunk_size") or 4),
            "--continue-on-error",
            "--shard-id",
            "0",
            "--shard-count",
            "1",
            "--write-shard-manifest",
        ]
        if matrix_cache_dir:
            command.extend(["--matrix-cache-dir", str(matrix_cache_dir), "--use-matrix-cache"])
        if max_formulas:
            command.extend(["--max-formulas", str(max_formulas)])
        job = ComputeJobSpec(
            job_id=job_id,
            job_kind=ComputeJobKind.FORMULA_BATCH_EVAL,
            command=command,
            output_dir=str(shard_output),
            required_device_type=required,
            gpu_count=1 if required == ComputeDeviceType.CUDA else 0,
            max_retries=int(config.get("max_retries") or 0),
            max_duration_seconds=config.get("max_duration_seconds"),
            shard_id=shard.shard_id,
            shard_count=shard.shard_count,
            data_freeze_id=config.get("data_freeze_id"),
            data_freeze_dir=config.get("data_freeze_dir"),
            metadata={"experiment_id": experiment_id, "workflow": workflow, "stage": ExperimentStage.FORMULA_BATCH_EVAL_SHARD},
        )
        jobs.append(job)
        nodes.append(ExperimentGraphNode(node_id=job_id, stage=ExperimentStage.FORMULA_BATCH_EVAL_SHARD, job_id=job_id, shard_id=shard.shard_id))
    merge_node_id = f"{experiment_id}_merge"
    nodes.append(ExperimentGraphNode(node_id=merge_node_id, stage=ExperimentStage.FORMULA_BATCH_EVAL_MERGE))
    for job in jobs:
        edges.append(ExperimentGraphEdge(source=job.job_id, target=merge_node_id))
    plan = ExperimentPlan(
        experiment_id=experiment_id,
        workflow=workflow,
        created_at=_utc_now(),
        output_dir=str(output_dir),
        shards=shards,
        graph_nodes=nodes,
        graph_edges=edges,
        compute_jobs=[job.to_dict() for job in jobs],
        resource_plan={
            "gpu_count_requested": gpu_count,
            "shard_count": shard_count,
            "device": device,
            "use_ddp_pretrain": bool(config.get("use_ddp_pretrain", False)),
        },
        metadata={k: v for k, v in config.items() if k not in {"jobs"}},
    )
    write_experiment_plan(plan, output_dir)
    return plan


def write_experiment_plan(plan: ExperimentPlan, output_dir: str | Path) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    write_json_artifact(output / "experiment_plan.json", plan.to_dict(), "experiment_plan", "experiment_orchestrator")
    write_json_artifact(
        output / "experiment_graph.json",
        {"nodes": [node.to_dict() for node in plan.graph_nodes], "edges": [edge.to_dict() for edge in plan.graph_edges]},
        "experiment_graph",
        "experiment_orchestrator",
    )
    write_json_artifact(output / "experiment_resource_plan.json", plan.resource_plan, "experiment_resource_plan", "experiment_orchestrator")
    write_jsonl_artifact(output / "experiment_shards.jsonl", [shard.to_dict() for shard in plan.shards], "experiment_shards", "experiment_orchestrator")
    write_json_artifact(output / "compute_jobs.json", {"jobs": plan.compute_jobs}, "compute_jobs_manifest", "experiment_orchestrator")
    return {
        "experiment_plan": str(output / "experiment_plan.json"),
        "experiment_graph": str(output / "experiment_graph.json"),
        "experiment_resource_plan": str(output / "experiment_resource_plan.json"),
        "experiment_shards": str(output / "experiment_shards.jsonl"),
        "compute_jobs": str(output / "compute_jobs.json"),
    }


def _create_alpha_large_plan(config: dict[str, Any], output_dir: Path, experiment_id: str, workflow: str) -> ExperimentPlan:
    readiness = _read_readiness(config.get("research_readiness_decision_path"))
    require_ready = bool(config.get("require_alpha_factory_ready"))
    blocked = require_ready and not bool(readiness.get("ready"))
    shard_count = max(1, int(config.get("shard_count") or 8))
    gpu_count = int(config.get("gpu_count") or 4)
    candidate_budget = int(config.get("candidate_budget") or config.get("max_formulas") or (shard_count * int(config.get("formulas_per_shard") or 12500)))
    formulas_per_shard = int(config.get("formulas_per_shard") or max(1, candidate_budget // shard_count))
    resource_plan = {
        "workflow": workflow,
        "status": "blocked" if blocked else "planned",
        "gpu_count_requested": gpu_count,
        "shard_count": shard_count,
        "formulas_per_shard": formulas_per_shard,
        "candidate_budget": candidate_budget,
        "device": config.get("device") or "cuda",
        "feature_set_name": config.get("feature_set_name") or "ashare_features_v2",
        "matrix_cache_dir": config.get("matrix_cache_dir"),
        "data_freeze_dir": config.get("data_freeze_dir"),
        "alpha_experiment_store_dir": config.get("alpha_experiment_store_dir"),
        "blocked_reason": "research readiness does not allow alpha factory" if blocked else "",
        "readiness": readiness,
    }
    nodes = [
        ExperimentGraphNode("readiness_gate", ExperimentStage.DATA_FREEZE_VALIDATE, metadata={"blocked": blocked, "readiness": readiness}),
        ExperimentGraphNode("alpha_factory_shards", ExperimentStage.FORMULA_BATCH_EVAL_SHARD, metadata={"shard_count": shard_count}),
        ExperimentGraphNode("factor_store_consolidation", ExperimentStage.FORMULA_BATCH_EVAL_MERGE),
    ]
    edges = [
        ExperimentGraphEdge("readiness_gate", "alpha_factory_shards"),
        ExperimentGraphEdge("alpha_factory_shards", "factor_store_consolidation"),
    ]
    jobs: list[dict[str, Any]] = []
    if not blocked:
        for shard_idx in range(shard_count):
            jobs.append(
                {
                    "job_id": f"{experiment_id}_alpha_factory_shard_{shard_idx:04d}",
                    "job_kind": "alpha_factory_shard_plan",
                    "shard_id": shard_idx,
                    "shard_count": shard_count,
                    "gpu_count": 1 if gpu_count > 0 else 0,
                    "formula_budget": formulas_per_shard,
                    "status": "planned",
                }
            )
    plan = ExperimentPlan(
        experiment_id=experiment_id,
        workflow=workflow,
        created_at=_utc_now(),
        output_dir=str(output_dir),
        shards=[],
        graph_nodes=nodes,
        graph_edges=edges,
        compute_jobs=jobs,
        resource_plan=resource_plan,
        metadata={k: v for k, v in config.items() if k not in {"jobs"}} | {"blocked": blocked, "readiness": readiness},
    )
    write_experiment_plan(plan, output_dir)
    _write_alpha_large_artifacts(plan, output_dir, resource_plan)
    return plan


def _write_alpha_large_artifacts(plan: ExperimentPlan, output_dir: Path, resource_plan: dict[str, Any]) -> None:
    payload = {
        "experiment_id": plan.experiment_id,
        "workflow": plan.workflow,
        "status": resource_plan["status"],
        "blocked": resource_plan["status"] == "blocked",
        "blocked_reason": resource_plan.get("blocked_reason", ""),
        "candidate_budget": resource_plan.get("candidate_budget", 0),
        "shard_count": resource_plan.get("shard_count", 0),
        "gpu_count_requested": resource_plan.get("gpu_count_requested", 0),
        "resource_plan": resource_plan,
        "compute_jobs": plan.compute_jobs,
    }
    write_json_artifact(output_dir / "alpha_large_campaign_plan.json", payload, "alpha_large_campaign_plan", "experiment_orchestrator")
    write_json_artifact(output_dir / "alpha_large_campaign_resource_plan.json", resource_plan, "alpha_large_campaign_resource_plan", "experiment_orchestrator")
    runbook = "\n".join(
        [
            "# Alpha Factory Large Campaign Runbook",
            "",
            f"Status: {payload['status']}",
            f"Blocked reason: {payload['blocked_reason'] or 'none'}",
            "",
            "This plan is generated only; it does not start compute jobs.",
        ]
    )
    (output_dir / "alpha_large_campaign_runbook.md").write_text(runbook, encoding="utf-8")
    commands = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "# Generated dry-run template. Review readiness and paths before executing manually.",
        f"# experiment_id={plan.experiment_id}",
        f"# shard_count={resource_plan.get('shard_count')}",
    ]
    (output_dir / "alpha_large_campaign_commands.sh").write_text("\n".join(commands) + "\n", encoding="utf-8")
    md = runbook + "\n"
    (output_dir / "alpha_large_campaign_plan.md").write_text(md, encoding="utf-8")


def _create_validation_large_plan(config: dict[str, Any], output_dir: Path, experiment_id: str, workflow: str) -> ExperimentPlan:
    readiness = _read_validation_readiness(config.get("research_readiness_decision_path"))
    require_ready = bool(config.get("require_validation_ready"))
    blocked = require_ready and not bool(readiness.get("ready"))
    shard_count = max(1, int(config.get("shard_count") or 1))
    candidate_budget = int(config.get("candidate_budget") or config.get("max_candidates") or 0)
    max_per_shard = int(config.get("max_candidates_per_shard") or (max(1, candidate_budget // shard_count) if candidate_budget else 0))
    resource_plan = {
        "workflow": workflow,
        "status": "blocked" if blocked else "planned",
        "shard_count": shard_count,
        "candidate_budget": candidate_budget,
        "max_candidates_per_shard": max_per_shard,
        "validation_campaign_store_dir": config.get("validation_campaign_store_dir"),
        "source_candidate_pool_path": config.get("source_candidate_pool_path"),
        "data_freeze_dir": config.get("data_freeze_dir"),
        "matrix_cache_dir": config.get("matrix_cache_dir"),
        "factor_store_dir": config.get("factor_store_dir"),
        "feature_set_name": config.get("feature_set_name") or "ashare_features_v2",
        "blocked_reason": "research readiness does not allow validation" if blocked else "",
        "readiness": readiness,
    }
    nodes = [
        ExperimentGraphNode("validation_readiness_gate", ExperimentStage.DATA_FREEZE_VALIDATE, metadata={"blocked": blocked, "readiness": readiness}),
        ExperimentGraphNode("validation_campaign_shards", ExperimentStage.WALK_FORWARD_BACKTEST_SHARD, metadata={"shard_count": shard_count}),
        ExperimentGraphNode("validation_campaign_consolidation", ExperimentStage.ARTIFACT_VALIDATION),
        ExperimentGraphNode("factor_certification_queue", ExperimentStage.ARTIFACT_VALIDATION),
    ]
    edges = [
        ExperimentGraphEdge("validation_readiness_gate", "validation_campaign_shards"),
        ExperimentGraphEdge("validation_campaign_shards", "validation_campaign_consolidation"),
        ExperimentGraphEdge("validation_campaign_consolidation", "factor_certification_queue"),
    ]
    jobs: list[dict[str, Any]] = []
    if not blocked:
        for shard_idx in range(shard_count):
            jobs.append(
                {
                    "job_id": f"{experiment_id}_validation_shard_{shard_idx:04d}",
                    "job_kind": "validation_campaign_shard_plan",
                    "shard_id": shard_idx,
                    "shard_count": shard_count,
                    "max_candidates": max_per_shard,
                    "status": "planned",
                }
            )
    plan = ExperimentPlan(
        experiment_id=experiment_id,
        workflow=workflow,
        created_at=_utc_now(),
        output_dir=str(output_dir),
        shards=[],
        graph_nodes=nodes,
        graph_edges=edges,
        compute_jobs=jobs,
        resource_plan=resource_plan,
        metadata={k: v for k, v in config.items() if k not in {"jobs"}} | {"blocked": blocked, "readiness": readiness},
    )
    write_experiment_plan(plan, output_dir)
    _write_validation_large_artifacts(plan, output_dir, resource_plan)
    return plan


def _write_validation_large_artifacts(plan: ExperimentPlan, output_dir: Path, resource_plan: dict[str, Any]) -> None:
    payload = {
        "experiment_id": plan.experiment_id,
        "workflow": plan.workflow,
        "status": resource_plan["status"],
        "blocked": resource_plan["status"] == "blocked",
        "blocked_reason": resource_plan.get("blocked_reason", ""),
        "candidate_budget": resource_plan.get("candidate_budget", 0),
        "shard_count": resource_plan.get("shard_count", 0),
        "resource_plan": resource_plan,
        "compute_jobs": plan.compute_jobs,
    }
    write_json_artifact(output_dir / "validation_large_campaign_plan.json", payload, "validation_large_campaign_plan", "experiment_orchestrator")
    write_json_artifact(
        output_dir / "validation_large_campaign_resource_plan.json",
        resource_plan,
        "validation_large_campaign_resource_plan",
        "experiment_orchestrator",
    )
    runbook = "\n".join(
        [
            "# Validation Campaign Large Plan Runbook",
            "",
            f"Status: {payload['status']}",
            f"Blocked reason: {payload['blocked_reason'] or 'none'}",
            "",
            "This plan is generated only; it does not start validation jobs.",
        ]
    )
    (output_dir / "validation_large_campaign_runbook.md").write_text(runbook + "\n", encoding="utf-8")
    commands = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "# Generated dry-run template. Review readiness and paths before executing manually.",
        f"# experiment_id={plan.experiment_id}",
        f"# shard_count={resource_plan.get('shard_count')}",
    ]
    (output_dir / "validation_large_campaign_commands.sh").write_text("\n".join(commands) + "\n", encoding="utf-8")
    (output_dir / "validation_large_campaign_plan.md").write_text(runbook + "\n", encoding="utf-8")


def _create_production_candidate_plan(config: dict[str, Any], output_dir: Path, experiment_id: str, workflow: str) -> ExperimentPlan:
    readiness = _read_portfolio_readiness(config.get("research_readiness_decision_path"))
    require_ready = bool(config.get("require_validation_ready") or config.get("require_portfolio_ready"))
    blocked = require_ready and not bool(readiness.get("ready"))
    max_items = int(config.get("max_items") or config.get("max_candidates") or config.get("candidate_budget") or 0)
    resource_plan = {
        "workflow": workflow,
        "status": "blocked" if blocked else "planned",
        "shard_count": 0 if blocked else 1,
        "compute_job_count": 0 if blocked else 1,
        "max_items": max_items,
        "factor_certification_queue_path": config.get("factor_certification_queue_path"),
        "certified_factor_pool_path": config.get("certified_factor_pool_path"),
        "factor_certification_campaign_dir": config.get("factor_certification_campaign_dir"),
        "portfolio_campaign_dir": config.get("portfolio_campaign_dir"),
        "data_freeze_dir": config.get("data_freeze_dir"),
        "matrix_cache_dir": config.get("matrix_cache_dir"),
        "blocked_reason": "portfolio campaign readiness is not satisfied" if blocked else "",
        "readiness": readiness,
    }
    nodes = [
        ExperimentGraphNode("portfolio_readiness_gate", ExperimentStage.DATA_FREEZE_VALIDATE, metadata={"blocked": blocked, "readiness": readiness}),
        ExperimentGraphNode("factor_certification_campaign", ExperimentStage.ARTIFACT_VALIDATION),
        ExperimentGraphNode("portfolio_campaign", ExperimentStage.WALK_FORWARD_BACKTEST_SHARD),
        ExperimentGraphNode("production_candidate_bundle", ExperimentStage.ARTIFACT_VALIDATION),
    ]
    edges = [
        ExperimentGraphEdge("portfolio_readiness_gate", "factor_certification_campaign"),
        ExperimentGraphEdge("factor_certification_campaign", "portfolio_campaign"),
        ExperimentGraphEdge("portfolio_campaign", "production_candidate_bundle"),
    ]
    jobs: list[dict[str, Any]] = []
    if not blocked:
        jobs.append(
            {
                "job_id": f"{experiment_id}_production_candidate_campaign",
                "job_kind": workflow,
                "max_items": max_items,
                "status": "planned",
            }
        )
    plan = ExperimentPlan(
        experiment_id=experiment_id,
        workflow=workflow,
        created_at=_utc_now(),
        output_dir=str(output_dir),
        shards=[],
        graph_nodes=nodes,
        graph_edges=edges,
        compute_jobs=jobs,
        resource_plan=resource_plan,
        metadata={k: v for k, v in config.items() if k not in {"jobs"}} | {"blocked": blocked, "readiness": readiness},
    )
    write_experiment_plan(plan, output_dir)
    _write_production_candidate_plan_artifacts(plan, output_dir, resource_plan)
    return plan


def _write_production_candidate_plan_artifacts(plan: ExperimentPlan, output_dir: Path, resource_plan: dict[str, Any]) -> None:
    payload = {
        "experiment_id": plan.experiment_id,
        "workflow": plan.workflow,
        "status": resource_plan["status"],
        "blocked": resource_plan["status"] == "blocked",
        "blocked_reason": resource_plan.get("blocked_reason", ""),
        "resource_plan": resource_plan,
        "compute_jobs": plan.compute_jobs,
    }
    if plan.workflow == "factor_certification_campaign_plan":
        filename = "certification_campaign_plan.json"
        artifact_type = "certification_campaign_plan"
        title = "Factor Certification Campaign Plan"
    elif plan.workflow == "portfolio_certification_campaign_plan":
        filename = "portfolio_campaign_plan.json"
        artifact_type = "portfolio_campaign_plan"
        title = "Portfolio Certification Campaign Plan"
    else:
        filename = "production_candidate_bundle_plan.json"
        artifact_type = "production_candidate_bundle_plan"
        title = "Production Candidate Bundle Plan"
    write_json_artifact(output_dir / filename, payload, artifact_type, "experiment_orchestrator")
    write_json_artifact(output_dir / "resource_plan.json", resource_plan, "production_candidate_resource_plan", "experiment_orchestrator")
    runbook = "\n".join(
        [
            f"# {title} Runbook",
            "",
            f"Status: {payload['status']}",
            f"Blocked reason: {payload['blocked_reason'] or 'none'}",
            "",
            "This plan is generated only; it does not start certification or portfolio jobs.",
        ]
    )
    (output_dir / "runbook.md").write_text(runbook + "\n", encoding="utf-8")
    commands = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "# Generated dry-run template. Review readiness and paths before executing manually.",
        f"# experiment_id={plan.experiment_id}",
        f"# workflow={plan.workflow}",
    ]
    (output_dir / "commands.sh").write_text("\n".join(commands) + "\n", encoding="utf-8")
    (output_dir / filename.replace(".json", ".md")).write_text(runbook + "\n", encoding="utf-8")


def _read_readiness(path: str | None) -> dict[str, Any]:
    if not path:
        return {"ready": True, "status": "not_required", "path": None}
    target = Path(path)
    if not target.exists():
        return {"ready": False, "status": "missing", "path": str(target)}
    payload = json.loads(target.read_text(encoding="utf-8"))
    ready = _truthy(payload.get("can_run_core_alpha_factory")) or _truthy(payload.get("can_run_expanded_alpha_factory"))
    ready = ready or _truthy(payload.get("alpha_ready"))
    status = str(payload.get("status", "") or "")
    ready = ready or status in {"alpha_factory_ready", "ready_for_alpha_factory", "ready", "pass"}
    return {"ready": bool(ready), "status": status, "path": str(target), "summary": payload.get("summary", {})}


def _read_validation_readiness(path: str | None) -> dict[str, Any]:
    if not path:
        return {"ready": True, "status": "not_required", "path": None}
    target = Path(path)
    if not target.exists():
        return {"ready": False, "status": "missing", "path": str(target)}
    payload = json.loads(target.read_text(encoding="utf-8"))
    ready = _truthy(payload.get("can_run_validation")) or _truthy(payload.get("can_run_validation_lab"))
    ready = ready or _truthy(payload.get("validation_ready"))
    status = str(payload.get("status", "") or "")
    ready = ready or status in {"validation_ready", "ready_for_validation", "ready", "pass"}
    return {"ready": bool(ready), "status": status, "path": str(target), "summary": payload.get("summary", {})}


def _read_portfolio_readiness(path: str | None) -> dict[str, Any]:
    if not path:
        return {"ready": True, "status": "not_required", "path": None}
    target = Path(path)
    if not target.exists():
        return {"ready": False, "status": "missing", "path": str(target)}
    payload = json.loads(target.read_text(encoding="utf-8"))
    ready = _truthy(payload.get("portfolio_ready")) or _truthy(payload.get("can_run_portfolio_campaign"))
    ready = ready or _truthy(payload.get("validation_ready")) or _truthy(payload.get("can_run_validation"))
    status = str(payload.get("status", "") or "")
    ready = ready or status in {"portfolio_ready", "ready_for_portfolio", "validation_ready", "ready", "pass"}
    return {"ready": bool(ready), "status": status, "path": str(target), "summary": payload.get("summary", {})}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "ready", "pass", "ok"}
    return False


def _write_inline_corpus(output_dir: Path, idx: int) -> Path:
    path = output_dir / f"inline_corpus_{idx}.jsonl"
    token = FORMULA_VOCAB.encode_name("RET_1D")
    path.write_text(json.dumps({"formula_hash": f"inline_{idx}", "formula_tokens": [token], "formula_names": ["RET_1D"]}) + "\n", encoding="utf-8")
    return path


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
